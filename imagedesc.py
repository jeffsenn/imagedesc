import argparse
import os
import pty
import subprocess
import fcntl
import termios
import time
import sys
import re
import shlex
import tempfile

#requires:
# ollama installed (assumes llava model also already there)
# brew install exiftool

## llava seems to hallucinate wildly on HEIC images, so we convert

## ?todo: possibly also downsize huge images...
## ?todo: adjust temp of model for more consistency
## ?todo: improve text extraction - it's flaky

## Mac Photos uses these exif keys:
#  XMP:Description
#  XMP:Subject (for keywords)
#    eg:   exiftool -overwrite_original -XMP:Subject="digital artwork" -XMP:Subject="character" -XMP:Subject="young woman"
# I chose XMP:OCRText for extracted text (still flaky)

DEBUG = 0

PROMPT1="Please generate a concise but detailed description for this image. Ensure the description meticulously covers all visible elements. Include details of any text, objects, people, colors, textures, and spatial relationships. Highlight contrasts, interactions, and any notable features that stand out. Avoid assumptions and focus only on what is clearly observable in the image "

old1_PROMPT1="Do not overly interpret. Describe this image with great detail including all objects present, the background, each person, each animal, and poses "
PROMPT2="given the description provide a comma separated list of keywords from the description but do not describe the description itself\n"

PROMPT3="extract all text from the image say none if no text present\n"

def disable_echo(fd):
    """Disables terminal echo on the given file descriptor."""
    attrs = termios.tcgetattr(fd)
    attrs[3] = attrs[3] & ~termios.ECHO  # Disable ECHO mode
    termios.tcsetattr(fd, termios.TCSANOW, attrs)
    
def set_nonblocking(fd):
    flags = fcntl.fcntl(fd, fcntl.F_GETFL)
    fcntl.fcntl(fd, fcntl.F_SETFL, flags | os.O_NONBLOCK)

def remove_ansi_escape_codes(text):
    ansi_escape_pattern = re.compile(r'\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])')
    return ansi_escape_pattern.sub('', text)

def read_nonblocking(fd, marker=">>>"):
    response = ""
    while True:
        try:
            chunk = os.read(fd, 4096).decode("utf-8")  # Read available output
            if not chunk:
                break  # Stop if no more output
            response += chunk
            if DEBUG: sys.stdout.write(chunk)  # Print in real-time
            if DEBUG: sys.stdout.flush()
            if marker in response:
                break
        except BlockingIOError:
            time.sleep(0.05)  # No new data, wait briefly
        except OSError:
            break  # No more data to read
    return response.strip()

def run_ollama_with_pty(image_path, follow_up=None):
    master_fd, slave_fd = pty.openpty()
    try:
        #disable_echo(master_fd)
        #disable_echo(slave_fd)
        # Start Ollama process with PTY
        process = subprocess.Popen(
            ["ollama", "run", "llava"],
            stdin=slave_fd,
            stdout=slave_fd,
            stderr=slave_fd,
            text=True,
            bufsize=0,
            close_fds=True
        )
        set_nonblocking(master_fd)
        
        wait1 = read_nonblocking(master_fd, marker=">>>")
        if DEBUG: print("\n\n\nWAIT\n", wait1)
        
        initial_prompt = PROMPT1 + shlex.quote(image_path) + "\n"
        os.write(master_fd, initial_prompt.encode())

        if DEBUG: print("\nWaiting for initial description...\n")
        initial_response = read_nonblocking(master_fd, marker=">>>")
        if DEBUG: print("\nInitial Description:\n", initial_response)
        os.write(master_fd, PROMPT2.encode())
        if DEBUG: print("\nWaiting for follow-up response...\n")
        follow_up_response = read_nonblocking(master_fd, marker=">>>")

        os.write(master_fd, PROMPT3.encode())
        if DEBUG: print("\nWaiting for 2nd follow-up response...\n")
        ocr_resp = read_nonblocking(master_fd, marker=">>>")
        
        #os.write(master_fd, "/bye\n".encode())
    finally:
        if DEBUG: print("\n closing....")
        os.close(master_fd)
        os.close(slave_fd)

    initial_response = remove_ansi_escape_codes(initial_response)
    initial_response = initial_response.replace("\r","")
    initial_response = re.sub(r'[^\x00-\x7F]+', '', initial_response)
        
    follow_up_response = remove_ansi_escape_codes(follow_up_response)
    follow_up_response = follow_up_response.replace("\r","")
    follow_up_response= re.sub(r'[^\x00-\x7F]+', '', follow_up_response)
    
    ocr_resp = remove_ansi_escape_codes(ocr_resp)
    ocr_resp = ocr_resp.replace("\r","")
    ocr_resp= re.sub(r'[^\x00-\x7F]+', '', ocr_resp)

    if DEBUG:
        print(repr(initial_response))
        print(repr(follow_up_response))
        print(repr(ocr_resp))        
        
    def strip_thinking(s):
        return re.split(r'\n.\.\.\ [^\n]*',s)[-1]
    def strip_ending(s):        
        return s.split("\n>>>",2)[0]

    initial_response = strip_ending(strip_thinking(initial_response))
    follow_up_response = strip_ending(strip_thinking(follow_up_response))
    ocr_resp = strip_ending(strip_thinking(ocr_resp))

    if initial_response.startswith("\nAdded image"):
        initial_response = initial_response.split("\n",3)[-1]

    initial_response = initial_response.strip()
    follow_up_response = follow_up_response.strip()
    ocr_resp = ocr_resp.strip()
    if ocr_resp.lower().startswith("none"): ocr_resp = ''
    ocr_resp = ocr_resp.replace('"','').strip()
    return initial_response, follow_up_response, ocr_resp


def run_shell_command(command, args=[]):
    try:
        result = subprocess.run(
            [command] + args,  # Command and arguments
            stdout=subprocess.PIPE,  # Capture standard output
            stderr=subprocess.PIPE,  # Capture standard error
            text=True,  # Return output as text
            check=True  # Raise an error if the command fails
        )
        print(f"Command executed successfully:\n{result.stdout}")
        return result.returncode  # Exit status
    except subprocess.CalledProcessError as e:
        print(f"Error executing command:\n{e.stderr}")
        return e.returncode  # Return non-zero exit code on failure

# Example usage
if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Image Enhance with Keywords and Description by writing back exif tags - requires ollama and exiftool")
    parser.add_argument(
        "--preserve", 
        action="store_true", 
        help="Do not overwrite original"
    )
    parser.add_argument(
        "--debug", 
        action="store_true", 
        help="Debug"
    )
    parser.add_argument(
        "--write", 
        action="store_true", 
        help="Actually write EXIF"
    )
    parser.add_argument(
        "filename", 
        type=str, 
        help="The name of the image file to process"
    )
    args = parser.parse_args()
    if args.debug: DEBUG = 1

    img_file = args.filename
    result = subprocess.run(["file", "-b", args.filename], capture_output=True, text=True)
    if "HEIF" in result.stdout:
        # convert to jpg - doesn't work w/o correct extension
        img_file = tempfile.mktemp() + ".jpg"
        result = subprocess.run(
            ["sips","-s","format","jpeg",args.filename,"--out",img_file],
            capture_output=True, text=True)
    
    initial_desc, follow_up_desc, ocr = run_ollama_with_pty(img_file)
    
    if args.preserve:
        cmdargs = []
    else:
        cmdargs = ["-overwrite_original"]
    initial_desc = initial_desc.replace('"','')
    cmdargs.append('-XMP:Description="'+initial_desc+'"')
    keywords=[x.strip().replace('"','') for x in follow_up_desc.split(',')]
    cmdargs.extend(['-XMP:Subject="'+kw+'"' for kw in keywords])
    if ocr:
        cmdargs.append('-XMP:OCRText="'+ocr+'"')
    cmdargs.append(args.filename)
    if not args.write:
        print("Use --write to save: ", cmdargs)
        sys.exit(1)
    else:
        exit_code = run_shell_command("exiftool", cmdargs)
        if exit_code != 0:
            print(f"Error {exit_code}")
        sys.exit(exit_code)
    
