#!/usr/bin/env python3

import subprocess
import sys
import platform
import os
import json # Added import

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
STATE_FILE = os.path.join(os.getcwd(), ".setup_state.json") # Define state file path

def check_setup_completion():
    if not os.path.exists(STATE_FILE):
        print(f"{Colors.RED}❌ Error: Setup state file ('{STATE_FILE}') not found.{Colors.ENDC}")
        print(f"{Colors.YELLOW}⚠️ Suna setup has likely not been run or completed.{Colors.ENDC}")
        print(f"{Colors.YELLOW}Please run 'python setup.py' to configure Suna and apply database migrations.{Colors.ENDC}")
        sys.exit(1)

    try:
        with open(STATE_FILE, 'r') as f:
            state = json.load(f)
    except (IOError, json.JSONDecodeError) as e:
        print(f"{Colors.RED}❌ Error: Could not load or parse setup state file ('{STATE_FILE}'): {e}{Colors.ENDC}")
        print(f"{Colors.YELLOW}⚠️ This may indicate an incomplete or corrupted setup process.{Colors.ENDC}")
        print(f"{Colors.YELLOW}Please re-run 'python setup.py'. If the issue persists, you may need to delete '.setup_state.json' and run setup again.{Colors.ENDC}")
        sys.exit(1)

    if not state.get('supabase_setup_completed', False):
        print(f"{Colors.RED}❌ Error: Supabase database setup is not marked as complete in the setup state.{Colors.ENDC}")
        print(f"{Colors.YELLOW}⚠️ Database migrations have likely not been applied successfully.{Colors.ENDC}")
        print(f"{Colors.YELLOW}Please run 'python setup.py' to ensure the database is correctly set up.{Colors.ENDC}")
        sys.exit(1)

    print(f"{Colors.GREEN}✅ Supabase setup verified as complete from state file. Proceeding...{Colors.ENDC}")


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

    # NEW: Perform setup completion check
    check_setup_completion() # This will exit if setup is not complete

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
