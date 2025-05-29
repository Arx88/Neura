#!/usr/bin/env python3

import subprocess
import sys
import platform
import os

# ANSI colors for pretty output
class Colors:
    HEADER = '\033[95m'
    BLUE = '\033[94m'
    CYAN = '\033[96m'
    GREEN = '\033[92m'
    YELLOW = '\033[93m'
    RED = '\033[91m'
    ENDC = '\033[0m'
    BOLD = '\033[1m'
    UNDERLINE = '\033[4m'

IS_WINDOWS = platform.system() == 'Windows'

def check_docker_compose_up():
    result = subprocess.run(
        ["docker", "compose", "ps", "-q"],
        capture_output=True,
        text=True,
        shell=IS_WINDOWS
    )
    return len(result.stdout.strip()) > 0

def main():
    # Check for backend/.env file
    env_path = os.path.join('backend', '.env')
    if not os.path.exists(env_path):
        print(f"{Colors.RED}❌ Error: Configuration file 'backend/.env' not found.{Colors.ENDC}")
        print(f"{Colors.YELLOW}⚠️ Please run 'python setup.py install' or 'python setup.py' completely to generate this file.{Colors.ENDC}")
        sys.exit(1)

    force = False
    if "--help" in sys.argv:
        print("Usage: ./start.py [OPTION]")
        print("Manage docker-compose services interactively")
        print("\nOptions:")
        print("  -f\tForce start containers without confirmation")
        print("  --help\tShow this help message")
        return
    if "-f" in sys.argv:
        force = True
        print(f"{Colors.YELLOW}Force awakened. Skipping confirmation.{Colors.ENDC}")

    is_up = check_docker_compose_up()

    if is_up:
        action = "stop"
        msg = f"{Colors.YELLOW}🛑 Stop containers? [y/N] {Colors.ENDC}"
    else:
        action = "start"
        msg = f"{Colors.GREEN}⚡ Start containers? [Y/n] {Colors.ENDC}"

    if not force:
        response = input(msg).strip().lower()
        if action == "stop":
            # Only proceed if user explicitly types 'y'
            if response != "y":
                print(f"{Colors.RED}Aborting.{Colors.ENDC}")
                return
        else:
            # Proceed unless user types 'n'
            if response == "n":
                print(f"{Colors.RED}Aborting.{Colors.ENDC}")
                return

    if action == "stop":
        print(f"{Colors.BLUE}Stopping containers...{Colors.ENDC}")
        subprocess.run(["docker", "compose", "down"], shell=IS_WINDOWS)
        print(f"{Colors.GREEN}Containers stopped.{Colors.ENDC}")
    else:
        print(f"{Colors.BLUE}Starting containers...{Colors.ENDC}")
        subprocess.run(["docker", "compose", "up", "-d"], shell=IS_WINDOWS)
        print(f"{Colors.GREEN}Containers started.{Colors.ENDC}")

if __name__ == "__main__":
    main()
