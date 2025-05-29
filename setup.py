#!/usr/bin/env python3
import os
import sys
import time
import platform
import subprocess
from getpass import getpass
import re

IS_WINDOWS = platform.system() == 'Windows'
if IS_WINDOWS:
    import winreg

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

TESSERACT_OPT_OUT_FLAG_FILE = os.path.expanduser("~/.suna_skip_tesseract_check")

# Helper function to run winget install
def run_winget_install(package_id, package_name):
    """
    Attempts to install a package using winget.
    Returns a tuple: (success: bool, already_installed: bool)
    """
    if not IS_WINDOWS:
        return False, False

    try:
        subprocess.run(['winget', '--version'], stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True, shell=True)
        print_info(f"winget is available. Attempting to install {package_name}...")
    except (subprocess.SubprocessError, FileNotFoundError):
        print_warning("winget command not found. Please install winget or install the tool manually.")
        return False, False

    already_installed_flag = False

    def _check_install_status(process_result, pkg_name):
        nonlocal already_installed_flag
        stdout = process_result.stdout.lower() if process_result.stdout else ""
        stderr = process_result.stderr.lower() if process_result.stderr else ""

        # Winget error codes: https://learn.microsoft.com/en-us/windows/package-manager/winget/return-codes
        # 0x8A15002B / 2316632107 / -2011873237 (SCHED_E_TASK_TERMINATED) - sometimes means already installed or needs elevation
        # 0x8A150018 / 2316632088 / -2011873264 (INSTALL_PACKAGE_IN_USE)
        # Common strings indicating already installed (these can vary by winget version and locale)
        already_installed_strings = [
            "already installed",  # English
            "package already installed", # English
            "no applicable upgrade found", # English
            "no upgrade available", # English
            "found an existing package if this is not the intended application", # More verbose
            # Add other language strings if necessary, e.g.:
            # "ya está instalado", # Spanish
            # "déjà installé", # French
        ]
        # Specific error codes that might imply "already installed" or "no action needed"
        # WINGET_ERROR_ALREADY_INSTALLED (0x8A150057) is not directly listed in public docs but observed.
        # Some sources suggest 0x80070000 related codes.
        # For Python specifically, "Python 3.11.X is already installed."
        python_specific_already_installed = f"{pkg_name.lower()} is already installed" # More specific for Python

        if python_specific_already_installed in stdout or python_specific_already_installed in stderr:
            print_info(f"{pkg_name} is already installed (detected by specific message).")
            already_installed_flag = True
            return True # Treat as success

        for s in already_installed_strings:
            if s in stdout or s in stderr:
                print_info(f"{pkg_name} is already installed or no upgrade needed (detected by string: '{s}').")
                already_installed_flag = True
                return True # Treat as success

        # Check for specific return codes that indicate "already installed" or similar non-failure states
        # NoApplicableUpgrade 0x8A15010E / 2316632334
        if process_result.returncode == 0x8A15010E: # NoApplicableUpgrade
            print_info(f"{pkg_name} has no applicable upgrade. Assuming already installed or latest.")
            already_installed_flag = True
            return True

        # If return code is 0, it's a success regardless of "already installed" strings
        if process_result.returncode == 0:
            print_info(f"winget install command output for {pkg_name}:\n{process_result.stdout}")
            print_success(f"{pkg_name} installation via winget successful.")
            return True

        # Handle specific error codes before generic failure
        if process_result.returncode == 2316632107: # 0x8A15002B (SCHED_E_TASK_TERMINATED)
            print_warning(f"Winget returned exit code 2316632107 (SCHED_E_TASK_TERMINATED) for {pkg_name}.")
            print_warning("This can sometimes mean the package is already installed, or it might indicate an issue.")
            print_warning("Consider it a potential pre-existing installation. If issues persist, manual check is advised.")
            # We might heuristically decide this means already_installed if other clues exist,
            # but for now, let's not set already_installed_flag unless a string confirms it.
            # Let's return True to allow the script to proceed, but not mark as 'already_installed' unless a string confirms.
            # This is a tricky case. If this code means "installed" for Python, we need to catch it.
            # However, if it means "failed", we should return False.
            # Given the ambiguity, let's be conservative for now and not assume it means "already installed"
            # unless a string also indicates it.
            # If Python install fails with this and no string, then the outer logic will catch it.
            # For Python, if winget says "Python 3.11.X is already installed" AND this code, it's fine.
            # If it's just this code, it's ambiguous.
            # Let's assume if this code appears, it's a success (as in, no need to retry with different flags for now)
            # but *not* necessarily "already_installed".
            # The function will return (True, already_installed_flag)
            return True # Tentative success, let the caller decide based on `already_installed_flag`

        # Generic failure
        print_error(f"winget installation of {pkg_name} failed with exit code {process_result.returncode}.")
        if process_result.stdout:
            print_error(f"Winget stdout:\n{process_result.stdout}")
        if process_result.stderr:
            print_error(f"Winget stderr:\n{process_result.stderr}")
        return False


    try:
        install_command = [
            'winget', 'install', package_id,
            '-s', 'winget',
            '--accept-package-agreements',
            '--accept-source-agreements',
            '--disable-interactivity'
        ]
        print_info(f"Executing winget command: {' '.join(install_command)}")
        process = subprocess.run(install_command, capture_output=True, text=True, check=False, shell=True)

        if _check_install_status(process, package_name):
            return True, already_installed_flag
        else:
            # If initial attempt failed and wasn't due to "already installed"
            # Check for access denied, which is a common issue for winget
            stderr_lower = process.stderr.lower() if process.stderr else ""
            if "0x80070005" in stderr_lower or "access is denied" in stderr_lower or "error 0x80070005" in stderr_lower:
                print_warning("Winget may require administrator privileges. The first attempt failed with an access denied error.")
                print_warning("Please try running this script in an administrator terminal if issues persist.")
            
            # Fallback: Try without --disable-interactivity as it sometimes helps
            # This is only if the first attempt truly failed (not already installed)
            print_info(f"Retrying winget install for {package_name} without --disable-interactivity...")
            install_command_fallback = [
                'winget', 'install', package_id,
                '-s', 'winget',
                '--accept-package-agreements',
                '--accept-source-agreements'
            ]
            print_info(f"Executing winget command (fallback): {' '.join(install_command_fallback)}")
            process_fallback = subprocess.run(install_command_fallback, capture_output=True, text=True, check=False, shell=True)

            if _check_install_status(process_fallback, package_name):
                return True, already_installed_flag # already_installed_flag would be set by _check_install_status
            else:
                # If fallback also failed
                print_error(f"Fallback winget install for {package_name} also failed.")
                return False, False # Definitely failed

    except Exception as e: # Catches other unexpected errors like if winget itself is not runnable after the initial check
        print_error(f"An unexpected error occurred during winget installation of {package_name}: {e}")
        return False, False

def print_banner():
    """Print Suna setup banner"""
    print(f"""
{Colors.BLUE}{Colors.BOLD}
   ███████╗██╗   ██╗███╗   ██╗ █████╗ 
   ██╔════╝██║   ██║████╗  ██║██╔══██╗
   ███████╗██║   ██║██╔██╗ ██║███████║
   ╚════██║██║   ██║██║╚██╗██║██╔══██║
   ███████║╚██████╔╝██║ ╚████║██║  ██║
   ╚══════╝ ╚═════╝ ╚═╝  ╚═══╝╚═╝  ╚═╝
                                      
   Setup Wizard
{Colors.ENDC}
""")

def print_step(step_num, total_steps, step_name):
    """Print a step header"""
    print(f"\n{Colors.BLUE}{Colors.BOLD}Step {step_num}/{total_steps}: {step_name}{Colors.ENDC}")
    print(f"{Colors.CYAN}{'='*50}{Colors.ENDC}\n")

def print_info(message):
    """Print info message"""
    print(f"{Colors.CYAN}ℹ️  {message}{Colors.ENDC}")

def print_success(message):
    """Print success message"""
    print(f"{Colors.GREEN}✅  {message}{Colors.ENDC}")

def print_warning(message):
    """Print warning message"""
    print(f"{Colors.YELLOW}⚠️  {message}{Colors.ENDC}")

def print_error(message):
    """Print error message"""
    print(f"{Colors.RED}❌  {message}{Colors.ENDC}")

def check_requirements():
    """Check if all required tools are installed"""
    requirements = {
        # Tool name: (URL, winget_package_id, winget_package_name, specific_version_check_command (optional))
        'git': ('https://git-scm.com/downloads', 'Git.Git', 'Git', None),
        # Python 3.11 check is now primarily handled by ensure_python_311_and_venv()
        # However, we keep an entry here to ensure 'python' (from venv) is reported as found.
        'python3': ('https://www.python.org/downloads/', 'Python.Python.3.11', 'Python 3.11', [sys.executable, '--version']),
        'pip3': ('https://pip.pypa.io/en/stable/installation/', None, 'pip3', None), # pip should come with python
        'node': ('https://nodejs.org/en/download/', 'OpenJS.NodeJS', 'Node.js (includes npm)', None), # Node includes npm
        'npm': ('https://docs.npmjs.com/downloading-and-installing-node-js-and-npm', None, 'npm', None), # npm check, but installed with Node
        'poetry': ('https://python-poetry.org/docs/#installation', 'PythonPoetry.Poetry', 'Poetry', None),
        'docker': ('https://docs.docker.com/get-docker/', None, 'Docker', None), # Docker handled separately
        'tesseract': ('https://github.com/UB-Mannheim/tesseract/wiki', 'UB-Mannheim.TesseractOCR', 'Tesseract OCR', None),
    }
    
    missing = []
    installed_via_winget_needs_path_check = []

    # Tesseract opt-out flag file is defined globally as TESSERACT_OPT_OUT_FLAG_FILE

    for cmd, details in requirements.items():
        url, winget_id, winget_name, specific_version_check = details
        cmd_to_check = cmd.replace('3', '') if IS_WINDOWS and cmd in ['python3', 'pip3'] else cmd

        if cmd == 'tesseract' and os.path.exists(TESSERACT_OPT_OUT_FLAG_FILE):
            print_info(f"Tesseract OCR check/installation is skipped due to user opt-out flag: {TESSERACT_OPT_OUT_FLAG_FILE}")
            continue # Skip all processing for Tesseract

        try:
            # If we are in the venv, python3 check should use sys.executable and is implicitly 3.11
            if cmd == 'python3' and os.environ.get(VENV_ACTIVATION_MARKER) == "1":
                current_python_version = get_python_version(sys.executable)
                if current_python_version and "3.11" in current_python_version:
                    print_success(f"Python 3.11 ({current_python_version}) is active in the virtual environment.")
                else:
                    # This case should ideally not be reached if ensure_python_311_and_venv worked correctly.
                    print_error(f"Inside venv, but Python version is {current_python_version} (expected 3.11).")
                    missing.append((cmd, url)) # Add to missing to indicate a problem
                    continue # Next requirement
            elif cmd == 'python3': # Not in venv, or marker not set (should have been handled by ensure_python_311_and_venv)
                 # This path should ideally not be hit for python3 if ensure_python_311_and_venv is called first.
                 # If it is, it means ensure_python_311_and_venv didn't run or didn't exit upon failure.
                py_version = get_python_version('python')
                if py_version and "3.11" in py_version:
                    print_success(f"Python 3.11 ({py_version}) found in PATH.")
                elif IS_WINDOWS:
                    py_version_launcher = get_python_version('py -3.11')
                    if py_version_launcher and "3.11" in py_version_launcher:
                         print_success(f"Python 3.11 ({py_version_launcher}) found via 'py -3.11'.")
                    else:
                        # This will be caught by the FileNotFoundError below and attempt winget if applicable
                        raise FileNotFoundError("Python 3.11 not found via 'python' or 'py -3.11'")
                else: # Non-windows
                    raise FileNotFoundError("Python 3.11 not found in PATH")


            # Standard check for other tools, or if python3 check above passed through
            version_check_cmd = [cmd_to_check, '--version']
            if specific_version_check: # e.g. for python3, this now uses sys.executable if in venv
                subprocess.run(specific_version_check, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True, shell=IS_WINDOWS)
            else:
                subprocess.run(version_check_cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True, shell=IS_WINDOWS)
            
            print_success(f"{cmd} is installed")

            # If node is installed, assume npm is too, but verify npm separately if it's its own entry.
            if cmd == 'node' and 'npm' in requirements and not any(m[0] == 'npm' for m in missing):
                try:
                    subprocess.run(['npm', '--version'], stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True, shell=IS_WINDOWS)
                    print_success("npm is installed (comes with Node.js)")
                except (subprocess.SubprocessError, FileNotFoundError):
                    print_error("npm is not found, though Node.js seems installed. This is unexpected.")
                    if IS_WINDOWS:
                        print_info("Node.js installer should include npm. A PATH issue or incomplete installation might be the cause.")
                    missing.append(('npm', requirements['npm'][0]))


        except (subprocess.SubprocessError, FileNotFoundError) as e:
            original_error_msg = f"{cmd} is not installed or not found in PATH."
            if isinstance(e, subprocess.SubprocessError) and hasattr(e, 'stderr') and e.stderr:
                original_error_msg += f" Error: {e.stderr.strip()}"
            elif isinstance(e, FileNotFoundError) and cmd == 'python3':
                 # More specific message if ensure_python_311_and_venv should have handled it
                if os.environ.get(VENV_ACTIVATION_MARKER) == "1":
                    original_error_msg = f"Python 3.11 check failed using '{sys.executable}'. This is unexpected in the activated venv."
                else:
                    original_error_msg = "Python 3.11 is required but not found."
            
            print_error(original_error_msg)

            if IS_WINDOWS and cmd == 'python3' and os.environ.get(VENV_ACTIVATION_MARKER) != "1":
                # This specific block for installing Python via winget should only trigger
                # if ensure_python_311_and_venv somehow didn't run or failed to install/relaunch.
                # Generally, ensure_python_311_and_venv should handle Python 3.11 installation.
                print_info(f"Attempting to install {winget_name} for Python 3.11 using winget as a fallback...")
                winget_success, winget_already_installed = run_winget_install(winget_id, winget_name)
                if winget_success:
                    if winget_already_installed:
                        print_info(f"{winget_name} was already installed (reported by winget). A new terminal might be needed.")
                    else:
                        print_success(f"{winget_name} installation via winget seems successful.")
                    print_info("Please re-run the setup script in a new terminal for changes to take effect.")
                    sys.exit(0) # Exit for user to re-run
                else:
                    print_error(f"Automated installation of {winget_name} via winget failed.")
                    print_info(f"Please install {cmd} manually from {url}")
                    print_info("Ensure 'Add Python to PATH' is checked during installation if applicable.")
                    missing.append((cmd, url))
            elif IS_WINDOWS: # For other tools on Windows
                # Enhanced Tesseract detection for Windows
                if cmd == 'tesseract':
                    tesseract_exe_path = None
                    detection_method = None

                    # 1. Check TESSDATA_PREFIX environment variable
                    tessdata_prefix = os.environ.get('TESSDATA_PREFIX')
                    if tessdata_prefix:
                        print_info(f"TESSDATA_PREFIX found: {tessdata_prefix}")
                        # Assume Tesseract is one level above tessdata
                        potential_path = os.path.abspath(os.path.join(tessdata_prefix, '..'))
                        search_paths = [potential_path, os.path.join(potential_path, 'bin')]
                        for path in search_paths:
                            test_exe = os.path.join(path, 'tesseract.exe')
                            if os.path.isfile(test_exe):
                                try:
                                    subprocess.run([test_exe, '--version'], stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True, shell=True)
                                    tesseract_exe_path = test_exe
                                    detection_method = f"TESSDATA_PREFIX environment variable ({tessdata_prefix})"
                                    break
                                except (subprocess.SubprocessError, FileNotFoundError):
                                    pass
                        if tesseract_exe_path:
                            print_success(f"Tesseract found via {detection_method}")
                            # Update cmd_to_check to use the full path
                            requirements[cmd] = (url, winget_id, winget_name, [tesseract_exe_path, '--version'])
                            # Re-run the check with the full path
                            try:
                                subprocess.run([tesseract_exe_path, '--version'], stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True, shell=True)
                                print_success(f"{cmd} is installed (verified via {detection_method})")
                                continue # Skip to next requirement
                            except (subprocess.SubprocessError, FileNotFoundError):
                                print_error(f"Failed to verify Tesseract at {tesseract_exe_path} even after finding it.")
                                # Proceed to other methods or winget

                    # 2. Check common installation paths
                    if not tesseract_exe_path:
                        common_paths = [
                            os.path.join(os.environ.get('ProgramFiles', 'C:\\Program Files'), 'Tesseract-OCR'),
                            os.path.join(os.environ.get('ProgramFiles(x86)', 'C:\\Program Files (x86)'), 'Tesseract-OCR')
                        ]
                        for base_path in common_paths:
                            search_paths = [base_path, os.path.join(base_path, 'bin')]
                            for path in search_paths:
                                test_exe = os.path.join(path, 'tesseract.exe')
                                if os.path.isfile(test_exe):
                                    try:
                                        subprocess.run([test_exe, '--version'], stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True, shell=True)
                                        tesseract_exe_path = test_exe
                                        detection_method = f"common installation path ({path})"
                                        break
                                    except (subprocess.SubprocessError, FileNotFoundError):
                                        pass
                            if tesseract_exe_path:
                                break
                        if tesseract_exe_path:
                            print_success(f"Tesseract found via {detection_method}")
                            requirements[cmd] = (url, winget_id, winget_name, [tesseract_exe_path, '--version'])
                            try:
                                subprocess.run([tesseract_exe_path, '--version'], stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True, shell=True)
                                print_success(f"{cmd} is installed (verified via {detection_method})")
                                continue
                            except (subprocess.SubprocessError, FileNotFoundError):
                                print_error(f"Failed to verify Tesseract at {tesseract_exe_path} even after finding it.")

                    # 3. Check Windows Registry
                    if not tesseract_exe_path:
                        registry_keys = [
                            (winreg.HKEY_LOCAL_MACHINE, r'SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\tesseract.exe', ''),
                            (winreg.HKEY_LOCAL_MACHINE, r'SOFTWARE\DigiObjects\TesseractOCR', 'Path'),
                            (winreg.HKEY_LOCAL_MACHINE, r'SOFTWARE\DigiObjects\TesseractOCR', 'InstallationPath'),
                            (winreg.HKEY_CURRENT_USER, r'SOFTWARE\DigiObjects\TesseractOCR', 'Path'),
                            (winreg.HKEY_CURRENT_USER, r'SOFTWARE\DigiObjects\TesseractOCR', 'InstallationPath'),
                            (winreg.HKEY_LOCAL_MACHINE, r'SOFTWARE\Tesseract-OCR', 'Path'),
                            (winreg.HKEY_LOCAL_MACHINE, r'SOFTWARE\Tesseract-OCR', 'InstallationPath'),
                            (winreg.HKEY_CURRENT_USER, r'SOFTWARE\Tesseract-OCR', 'Path'),
                            (winreg.HKEY_CURRENT_USER, r'SOFTWARE\Tesseract-OCR', 'InstallationPath'),
                        ]
                        for hive, key_path, value_name in registry_keys:
                            try:
                                with winreg.OpenKey(hive, key_path) as key:
                                    reg_path, _ = winreg.QueryValueEx(key, value_name)
                                    if reg_path:
                                        # If value_name is empty, reg_path is the tesseract.exe itself for App Paths
                                        # Otherwise, reg_path is a directory.
                                        potential_exe_path = reg_path if not value_name else os.path.join(reg_path, 'tesseract.exe')
                                        
                                        # Normalize path and check if it's a file directly
                                        if os.path.isfile(potential_exe_path):
                                            test_exe = potential_exe_path
                                        else: # Check in bin subdirectory if the registry path was a directory
                                            if value_name: # Only if reg_path was a directory
                                                test_exe = os.path.join(reg_path, 'bin', 'tesseract.exe')
                                            else: # if value_name was empty, potential_exe_path was already the full path
                                                test_exe = None

                                        if test_exe and os.path.isfile(test_exe):
                                            print_info(f"Testing Tesseract from registry: {test_exe} (Key: {key_path}\\{value_name})")
                                            try:
                                                subprocess.run([test_exe, '--version'], stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True, shell=True)
                                                tesseract_exe_path = test_exe
                                                detection_method = f"Windows Registry ({key_path}\\{value_name})"
                                                break
                                            except (subprocess.SubprocessError, FileNotFoundError):
                                                print_warning(f"Found Tesseract via registry at {test_exe}, but '--version' check failed.")
                                                pass # Try next registry key
                            except FileNotFoundError:
                                pass # Key or value not found
                            except OSError as oe:
                                print_warning(f"Error accessing registry key {key_path}: {oe}") # Permissions or other OS error
                            if tesseract_exe_path:
                                break
                        if tesseract_exe_path:
                            print_success(f"Tesseract found via {detection_method}")
                            requirements[cmd] = (url, winget_id, winget_name, [tesseract_exe_path, '--version'])
                            try:
                                subprocess.run([tesseract_exe_path, '--version'], stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True, shell=True)
                                print_success(f"{cmd} is installed (verified via {detection_method})")
                                continue
                            except (subprocess.SubprocessError, FileNotFoundError):
                                print_error(f"Failed to verify Tesseract at {tesseract_exe_path} even after finding it.")
                    
                    # If Tesseract is still not found by custom methods, try Chocolatey
                    if not tesseract_exe_path:
                        try:
                            # Check if choco is available
                            subprocess.run(['choco', '--version'], stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True, shell=True)
                            print_info("Chocolatey found. Attempting to install Tesseract OCR using Chocolatey...")
                            choco_install_command = [
                                'choco', 'install', 'tesseract-ocr', '-y',
                                '--params', '"/InstallDir=C:\\Program Files\\Tesseract-OCR /Path"' # Ensure quotes for params
                            ]
                            # Note: Chocolatey installs to machine PATH by default, so a new terminal is usually needed.
                            # The /Path parameter for tesseract-ocr package specifically tries to ensure it.
                            # The /InstallDir is a suggestion; package maintainer decides if it's respected.
                            
                            process = subprocess.run(choco_install_command, capture_output=True, text=True, shell=True) # shell=True for choco
                            
                            if process.returncode == 0:
                                print_success("Tesseract OCR installation via Chocolatey seems successful.")
                                print_info(f"Chocolatey output:\n{process.stdout}")
                                # Add to re-check list, similar to winget installs
                                installed_via_winget_needs_path_check.append((cmd, url, cmd_to_check))
                                continue # Skip to next requirement (and skip winget)
                            else:
                                print_error(f"Chocolatey install of Tesseract OCR failed. Exit code: {process.returncode}")
                                if process.stdout:
                                    print_error(f"Choco stdout:\n{process.stdout}")
                                if process.stderr:
                                    print_error(f"Choco stderr:\n{process.stderr}")
                                # Proceed to winget as a further fallback
                        except (subprocess.SubprocessError, FileNotFoundError) as choco_e:
                            if isinstance(choco_e, FileNotFoundError):
                                print_info("Chocolatey (choco) not found. Skipping Chocolatey installation attempt.")
                            else:
                                print_warning(f"Error during Chocolatey check or install: {choco_e}. Proceeding to other methods.")
                    
                    # If Tesseract is still not found (after custom checks and choco attempt), then proceed with original logic (winget, etc.)
                    if tesseract_exe_path: # Should have 'continue'd if successful earlier
                        print_warning("Tesseract was found by custom detection but could not be verified. Proceeding with standard installation checks.")


                if cmd == 'docker':
                    print_error("Docker installation cannot be automated by this script.")
                    print_info("Please install Docker Desktop for Windows manually.")
                    print_info("  - Download from: https://www.docker.com/products/docker-desktop/")
                    print_info("  - Ensure WSL2 (Windows Subsystem for Linux 2) is enabled.")
                    print_info("    To enable WSL2, open PowerShell as Administrator and run: ")
                    print_info("    dism.exe /online /enable-feature /featurename:Microsoft-Windows-Subsystem-Linux /all /norestart")
                    print_info("    dism.exe /online /enable-feature /featurename:VirtualMachinePlatform /all /norestart")
                    print_info("    Then restart your computer and set WSL2 as default: wsl --set-default-version 2")
                    print_info("  - Enable hardware virtualization (VT-x or AMD-V) in your computer's BIOS/UEFI settings.")
                    print_info("After installing and starting Docker Desktop, please re-run this setup script.")
                    missing.append((cmd, url))
                elif winget_id:
                    print_info(f"Attempting to install {winget_name} using winget...")
                    if cmd == 'poetry': # Poetry special handling
                        # First, try pip install if python (from venv, so it's python 3.11) is available
                        try:
                            # Use sys.executable to ensure pip is called from the venv's Python
                            pip_install_cmd = [sys.executable, '-m', 'pip', 'install', 'poetry']
                            print_info(f"Attempting to install Poetry using: {' '.join(pip_install_cmd)}")
                            subprocess.run(pip_install_cmd, check=True, shell=IS_WINDOWS)
                            print_success("Poetry installed successfully using pip in the current environment.")
                            # Re-check poetry
                            try:
                                # If Poetry is installed into the venv's scripts, it should be found.
                                # On Windows, this might require the venv to be active in the PATH,
                                # or calling poetry via `python -m poetry`.
                                # For simplicity, let's assume if pip install works, poetry command will be available
                                # or poetry can be run via `python -m poetry`.
                                # We will rely on the later `poetry lock` and `poetry install` commands to truly fail
                                # if poetry is not usable.
                                subprocess.run(['poetry', '--version'], stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True, shell=IS_WINDOWS)
                                print_success("Poetry successfully verified after pip installation ('poetry --version').")
                                continue # Installed, so skip to next requirement
                            except (subprocess.SubprocessError, FileNotFoundError):
                                print_warning("Poetry installed via pip, but 'poetry --version' still fails directly.")
                                print_info("This might be a PATH issue if the venv's Scripts directory isn't in PATH, or if Poetry was installed globally by pip.")
                                print_info("Will attempt to use 'python -m poetry' for subsequent Poetry commands if direct call fails.")
                                # We don't add to winget_needs_path_check here as pip was the primary method.
                                # We assume it's installed for now and rely on later Poetry commands.
                                continue 
                        except subprocess.SubprocessError as poetry_pip_e:
                            print_warning(f"pip install poetry failed: {poetry_pip_e}. Attempting winget install for Poetry...")
                            winget_success_poetry, _ = run_winget_install(winget_id, winget_name) # already_installed doesn't matter as much here
                            if winget_success_poetry:
                                installed_via_winget_needs_path_check.append((cmd, url, cmd_to_check))
                            else:
                                print_error(f"Automated installation of {winget_name} via pip and winget also failed.")
                                print_info(f"Please install {cmd} manually from {url}")
                                print_info("For Poetry, the recommended method is often via pip or their official install script.")
                                missing.append((cmd, url))
                    else: # For other tools (not poetry, not python3 handled by ensure_python_311_and_venv)
                        winget_success_other, _ = run_winget_install(winget_id, winget_name)
                        if winget_success_other:
                            installed_via_winget_needs_path_check.append((cmd, url, cmd_to_check))
                        else: # All automated attempts for this tool (including winget) failed
                            if cmd == 'tesseract':
                            print_warning("All automated attempts to install Tesseract OCR have failed (PATH, common locations, registry, Chocolatey, winget).")
                            user_choice = input(f"{Colors.YELLOW}Would you like to skip Tesseract OCR requirement for now and in future Suna setups? (yes/no): {Colors.ENDC}").strip().lower()
                            if user_choice in ['yes', 'y']:
                                try:
                                    with open(TESSERACT_OPT_OUT_FLAG_FILE, 'w') as f:
                                        f.write("User opted out of Tesseract OCR check.")
                                    print_info(f"Tesseract OCR requirement will be skipped in future runs. Flag file created at: {TESSERACT_OPT_OUT_FLAG_FILE}")
                                    continue # Skip adding to missing list and manual instructions for this run
                                except IOError as e_io:
                                    print_error(f"Could not create opt-out flag file at {TESSERACT_OPT_OUT_FLAG_FILE}: {e_io}")
                                    print_info("Proceeding with manual installation instructions for Tesseract.")
                            # If user says no, or if flag creation failed, proceed to print manual instructions and add to missing list.
                        
                        print_error(f"Automated installation of {winget_name} failed or winget is not available.")
                        print_info(f"Please install {cmd} manually from {url}")
                        if cmd == 'python3':
                             print_info("Download the Python 3.11 installer from the URL and run it. Ensure 'Add Python to PATH' is checked during installation.")
                        elif cmd == 'git':
                             print_info("Download the Git installer from the URL and run it, accepting default options is usually fine.")
                        elif cmd == 'node':
                             print_info("Download the Node.js LTS installer from the URL and run it. This will also install npm.")
                        elif cmd == 'tesseract': 
                            # This block is now reached if:
                            # 1. All automated attempts failed (PATH, custom, choco, winget)
                            # 2. AND the user chose NOT to opt-out, or opt-out file creation failed.
                            print_error("Tesseract OCR is not installed or not found in PATH. This is required for some features unless opted out.")
                            print_info("Please install it manually:")
                            print_info("1. Download the installer from: https://github.com/UB-Mannheim/tesseract/wiki (look for Windows installers).")
                            print_info("2. Run the installer.")
                            print_info("3. Important: During installation, ensure you select the option to 'Add Tesseract to system PATH'. This might be under a component like 'Full installation' or a specific checkbox for PATH.")
                            print_info("   Alternatively, you can add it manually. Default path is often 'C:\\Program Files\\Tesseract-OCR'.")
                            print_info("4. After installation, you MUST restart this script or open a new terminal window for the PATH changes to take effect.")
                        missing.append((cmd, url))
                elif cmd == 'pip3' and any(m[0] == 'python3' for m in missing): # If Python failed, pip will also fail.
                    print_info("pip3 installation depends on Python. Python is not yet installed.")
                    missing.append((cmd,url))
                elif cmd == 'npm' and any(m[0] == 'node' for m in missing):
                     print_info("npm installation depends on Node.js. Node.js is not yet installed.")
                     missing.append((cmd,url))
                # elif cmd == 'tesseract': # Already handled by specific tesseract message above for failed winget
                #    pass # This is now handled by the more specific message above
                else: # No winget ID for this tool, or not docker
                    print_info(f"No automated Windows installation configured for {cmd}. Please install manually from {url}.")
                    missing.append((cmd, url))
            else: # Not windows
                if cmd == 'docker':
                     print_info(f"For Docker on non-Windows, please follow instructions at {url}")
                elif cmd == 'tesseract': # This is for non-Windows systems
                    print_warning("Tesseract OCR installation via system package manager or PATH check failed on this non-Windows system.")
                    user_choice = input(f"{Colors.YELLOW}Would you like to skip Tesseract OCR requirement for now and in future Suna setups? (yes/no): {Colors.ENDC}").strip().lower()
                    if user_choice in ['yes', 'y']:
                        try:
                            with open(TESSERACT_OPT_OUT_FLAG_FILE, 'w') as f:
                                f.write("User opted out of Tesseract OCR check.")
                            print_info(f"Tesseract OCR requirement will be skipped in future runs. Flag file created at: {TESSERACT_OPT_OUT_FLAG_FILE}")
                            continue # Skip adding to missing list and manual instructions
                        except IOError as e_io:
                            print_error(f"Could not create opt-out flag file at {TESSERACT_OPT_OUT_FLAG_FILE}: {e_io}")
                            print_info("Proceeding with manual installation instructions for Tesseract.")
                    
                    # If user did not opt-out or flag creation failed:
                    print_error("Tesseract OCR is not installed or not found in PATH.")
                    print_info("Please install Tesseract OCR for your OS from: https://github.com/UB-Mannheim/tesseract/wiki")
                    print_info("Ensure it's added to your system PATH.")
                    missing.append((cmd, url))
                else: # Other non-Windows tools that were not found
                    missing.append((cmd, url))

    # Re-check tools that were installed via package managers, as PATH might not have updated immediately
    if installed_via_winget_needs_path_check: # This list is used for Chocolatey installs too
        print_info("\nRe-checking tools installed via automated methods as PATH environment variable changes might require a new terminal session...")
        for cmd, url, cmd_to_check_again in installed_via_winget_needs_path_check:
            if cmd == 'tesseract' and os.path.exists(TESSERACT_OPT_OUT_FLAG_FILE):
                print_info(f"Skipping re-check for Tesseract due to opt-out flag: {TESSERACT_OPT_OUT_FLAG_FILE}")
                continue
            try:
                version_check_cmd = [cmd_to_check_again, '--version']
                if cmd == 'python3': # Re-check for Python (should be sys.executable if in venv)
                    # This re-check is mostly for consistency if winget installed it and a new shell was needed.
                    # If already in venv, this should pass easily.
                    py_version_recheck = get_python_version(sys.executable if os.environ.get(VENV_ACTIVATION_MARKER) == "1" else 'python')
                    if py_version_recheck and "3.11" in py_version_recheck:
                        print_success(f"Python 3.11 ({py_version_recheck}) successfully verified after potential installation.")
                    else:
                         raise FileNotFoundError(f"Python 3.11 not found or not the correct version ({py_version_recheck}) after automated install attempt.")
                elif cmd == 'poetry' and IS_WINDOWS: 
                    try:
                        # Try direct 'poetry --version' first
                        subprocess.run([cmd_to_check_again, '--version'], stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True, shell=IS_WINDOWS)
                        print_success(f"{cmd} successfully verified after installation ('{cmd_to_check_again} --version').")
                    except (subprocess.SubprocessError, FileNotFoundError):
                        # If direct call fails, try 'python -m poetry --version' as Poetry might be installed as a module
                        print_warning(f"'{cmd_to_check_again} --version' failed. Trying '{sys.executable} -m poetry --version'.")
                        try:
                            subprocess.run([sys.executable, '-m', 'poetry', '--version'], stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True, shell=IS_WINDOWS)
                            print_success(f"{cmd} successfully verified using '{sys.executable} -m poetry --version'.")
                        except (subprocess.SubprocessError, FileNotFoundError):
                            print_warning(f"{cmd} was reportedly installed (e.g., via pip or winget), but neither direct call nor '{sys.executable} -m poetry' works.")
                            print_info(f"This could be a PATH issue or incomplete installation.")
                            print_info(f"If installed via pip into a user script dir (e.g., %APPDATA%\\Python\\Python311\\Scripts on Windows), ensure that's in PATH.")
                            print_info(f"Please open a new terminal and re-run the setup. If the issue persists, manual PATH adjustment or reinstallation might be needed.")
                            missing.append((cmd,url))
                elif cmd == 'tesseract' and IS_WINDOWS:
                    try:
                        subprocess.run([cmd_to_check_again, '--version'], stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True, shell=IS_WINDOWS)
                        print_success(f"{cmd} successfully verified after automated installation.")
                    except (subprocess.SubprocessError, FileNotFoundError):
                        print_warning(f"{cmd} ('{cmd_to_check_again}') not immediately found in PATH after installation attempt.")
                        # Attempt temporary PATH modification for Tesseract
                        if cmd == 'tesseract':
                            default_tesseract_path = r"C:\Program Files\Tesseract-OCR"
                            if os.path.exists(default_tesseract_path):
                                print_info(f"Attempting to verify Tesseract by temporarily adding '{default_tesseract_path}' to PATH...")
                                temp_env = os.environ.copy()
                                temp_env['PATH'] = f"{default_tesseract_path}{os.pathsep}{temp_env.get('PATH', '')}"
                                try:
                                    subprocess.run([cmd_to_check_again, '--version'], env=temp_env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True, shell=IS_WINDOWS)
                                    print_success(f"Tesseract successfully verified using temporary PATH modification with '{default_tesseract_path}'.")
                                    print_info("This indicates Tesseract is likely installed correctly. Please open a new terminal for the PATH changes to take full effect system-wide.")
                                    # If successful here, we don't add to missing list for Tesseract
                                    continue # Move to the next item in installed_via_winget_needs_path_check
                                except (subprocess.SubprocessError, FileNotFoundError):
                                    print_error(f"Tesseract verification still failed even after temporarily adding '{default_tesseract_path}' to PATH.")
                                    # Fall through to add to missing list below
                            else:
                                print_info(f"Default Tesseract installation path '{default_tesseract_path}' not found. Skipping temporary PATH modification.")
                        
                        # Original messaging if temporary PATH check wasn't done or also failed
                        print_error(f"{cmd} was reportedly installed by an automated method, but is still not found in PATH.")
                        print_info("This is often due to the PATH environment variable not being updated in the current terminal session.")
                        print_info(f"Please open a new terminal/command prompt and re-run this setup script.")
                        if cmd == 'tesseract':
                             print_info(f"If the problem persists, ensure Tesseract OCR's installation directory (e.g., '{default_tesseract_path or 'C:\\Program Files\\Tesseract-OCR'}') is in your system PATH.")
                        missing.append((cmd, url))
                else: # For other tools, not tesseract
                    subprocess.run(version_check_cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True, shell=IS_WINDOWS)
                    print_success(f"{cmd} successfully verified after automated installation.")
            except (subprocess.SubprocessError, FileNotFoundError) as e:
                final_error_msg = f"Verification of {cmd} failed after automated installation attempt."
                if isinstance(e, FileNotFoundError) and cmd == 'python3':
                    final_error_msg = f"Python 3.11 was reportedly installed, but '{sys.executable if os.environ.get(VENV_ACTIVATION_MARKER) == '1' else 'python'} --version' still fails or shows wrong version."

                print_error(final_error_msg)
                if isinstance(e, subprocess.SubprocessError) and hasattr(e, 'stderr') and e.stderr:
                    print_error(f"Error details: {e.stderr.strip()}")
                
                print_info("This could be due to the PATH environment variable not being updated in the current terminal session.")
                print_info(f"Please open a new terminal/command prompt and re-run this setup script.")
                print_info(f"If the problem persists, you may need to manually adjust your PATH or ensure the correct version is selected (e.g., using pyenv or similar tools for Python).")
                missing.append((cmd, url))
                
    # Filter out npm from missing if node is also missing, as node includes npm
    if any(m[0] == 'node' for m in missing):
        missing = [m for m in missing if m[0] != 'npm']
    # Filter out pip3 from missing if python3 is also missing
    if any(m[0] == 'python3' for m in missing):
        missing = [m for m in missing if m[0] != 'pip3']

    # Remove duplicates from missing list while preserving order (important for messages)
    seen_missing = set()
    unique_missing = []
    for item in missing:
        if item[0] not in seen_missing:
            unique_missing.append(item)
            seen_missing.add(item[0])
    missing = unique_missing

    if missing:
        print_error("\nMissing required tools or failed verification after automated install attempts.")
        print_info("Please install them manually based on the instructions above or ensure they are correctly added to your PATH, then re-run this script.")
        for cmd, url in missing:
            print(f"  - {cmd}: {url}")
        sys.exit(1)
    
    return True

def check_docker_running():
    """Check if Docker is running"""
    try:
        result = subprocess.run(
            ['docker', 'info'],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=True,
            shell=IS_WINDOWS
        )
        print_success("Docker is running")
        return True
    except subprocess.SubprocessError:
        print_error("Docker is installed but not running. Please start Docker and try again.")
        sys.exit(1)

def check_suna_directory():
    """Check if we're in a Suna repository"""
    required_dirs = ['backend', 'frontend']
    required_files = ['README.md', 'docker-compose.yaml']
    
    for directory in required_dirs:
        if not os.path.isdir(directory):
            print_error(f"'{directory}' directory not found. Make sure you're in the Suna repository root.")
            return False
    
    for file in required_files:
        if not os.path.isfile(file):
            print_error(f"'{file}' not found. Make sure you're in the Suna repository root.")
            return False
    
    print_success("Suna repository detected")
    return True

def validate_url(url, allow_empty=False):
    """Validate a URL"""
    if allow_empty and not url:
        return True
    
    pattern = re.compile(
        r'^(?:http|https)://'  # http:// or https://
        r'(?:(?:[A-Z0-9](?:[A-Z0-9-]{0,61}[A-Z0-9])?\.)+(?:[A-Z]{2,6}\.?|[A-Z0-9-]{2,}\.?)|'  # domain
        r'localhost|'  # localhost
        r'\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3})'  # or IP
        r'(?::\d+)?'  # optional port
        r'(?:/?|[/?]\S+)$', re.IGNORECASE)
    
    return bool(pattern.match(url))

def validate_api_key(api_key, allow_empty=False):
    """Validate an API key (basic format check)"""
    if allow_empty and not api_key:
        return True
    
    # Basic check: not empty and at least 10 chars
    return bool(api_key)

def collect_supabase_info():
    """Collect Supabase information"""
    print_info("You'll need to create a Supabase project before continuing")
    print_info("Visit https://supabase.com/dashboard/projects to create one")
    print_info("After creating your project, visit the project settings -> Data API and you'll need to get the following information:")
    print_info("1. Supabase Project URL (e.g., https://abcdefg.supabase.co)")
    print_info("2. Supabase anon key")
    print_info("3. Supabase service role key")
    input("Press Enter to continue once you've created your Supabase project...")
    
    while True:
        supabase_url = input("Enter your Supabase Project URL (e.g., https://abcdefg.supabase.co): ")
        if validate_url(supabase_url):
            break
        print_error("Invalid URL format. Please enter a valid URL.")
    
    while True:
        supabase_anon_key = input("Enter your Supabase anon key: ")
        if validate_api_key(supabase_anon_key):
            break
        print_error("Invalid API key format. It should be at least 10 characters long.")
    
    while True:
        supabase_service_role_key = input("Enter your Supabase service role key: ")
        if validate_api_key(supabase_service_role_key):
            break
        print_error("Invalid API key format. It should be at least 10 characters long.")
    
    return {
        'SUPABASE_URL': supabase_url,
        'SUPABASE_ANON_KEY': supabase_anon_key,
        'SUPABASE_SERVICE_ROLE_KEY': supabase_service_role_key,
    }

def collect_daytona_info():
    """Collect Daytona API key"""
    print_info("You'll need to create a Daytona account before continuing")
    print_info("Visit https://app.daytona.io/ to create one")
    print_info("Then, generate an API key from 'Keys' menu")
    print_info("After that, go to Images (https://app.daytona.io/dashboard/images)")
    print_info("Click '+ Create Image'")
    print_info(f"Enter 'kortix/suna:0.1.2.8' as the image name")
    print_info(f"Set '/usr/bin/supervisord -n -c /etc/supervisor/conf.d/supervisord.conf' as the Entrypoint")

    input("Press Enter to continue once you've completed these steps...")
    
    while True:
        daytona_api_key = input("Enter your Daytona API key: ")
        if validate_api_key(daytona_api_key):
            break
        print_error("Invalid API key format. It should be at least 10 characters long.")
    
    return {
        'DAYTONA_API_KEY': daytona_api_key,
        'DAYTONA_SERVER_URL': "https://app.daytona.io/api",
        'DAYTONA_TARGET': "us",
    }

def collect_llm_api_keys():
    """Collect LLM API keys for various providers"""
    print_info("You need at least one LLM provider API key to use Suna")
    print_info("Available LLM providers: OpenAI, Anthropic, OpenRouter")
    
    # Display provider selection options
    print(f"\n{Colors.CYAN}Select LLM providers to configure:{Colors.ENDC}")
    print(f"{Colors.CYAN}[1] {Colors.GREEN}OpenAI{Colors.ENDC}")
    print(f"{Colors.CYAN}[2] {Colors.GREEN}Anthropic{Colors.ENDC}")
    print(f"{Colors.CYAN}[3] {Colors.GREEN}OpenRouter{Colors.ENDC} {Colors.CYAN}(access to multiple models){Colors.ENDC}")
    print(f"{Colors.CYAN}Enter numbers separated by commas (e.g., 1,2,3){Colors.ENDC}\n")

    while True:
        providers_input = input("Select providers (required, at least one): ")
        selected_providers = []
        
        try:
            # Parse the input, handle both comma-separated and space-separated
            provider_numbers = [int(p.strip()) for p in providers_input.replace(',', ' ').split()]
            
            for num in provider_numbers:
                if num == 1:
                    selected_providers.append('OPENAI')
                elif num == 2:
                    selected_providers.append('ANTHROPIC')
                elif num == 3:
                    selected_providers.append('OPENROUTER')
            
            if selected_providers:
                break
            else:
                print_error("Please select at least one provider.")
        except ValueError:
            print_error("Invalid input. Please enter provider numbers (e.g., 1,2,3).")

    # Collect API keys for selected providers
    api_keys = {}
    model_info = {}
    
    # Model aliases for reference
    model_aliases = {
        'OPENAI': ['openai/gpt-4o', 'openai/gpt-4o-mini'],
        'ANTHROPIC': ['anthropic/claude-3-7-sonnet-latest', 'anthropic/claude-3-5-sonnet-latest'],
        'OPENROUTER': ['openrouter/google/gemini-2.5-pro-preview', 'openrouter/deepseek/deepseek-chat-v3-0324:free', 'openrouter/openai/gpt-4o-2024-11-20'],
    }
    
    for provider in selected_providers:
        print_info(f"\nConfiguring {provider}")
        
        if provider == 'OPENAI':
            while True:
                api_key = input("Enter your OpenAI API key: ")
                if validate_api_key(api_key):
                    api_keys['OPENAI_API_KEY'] = api_key
                    
                    # Recommend default model
                    print(f"\n{Colors.CYAN}Recommended OpenAI models:{Colors.ENDC}")
                    for i, model in enumerate(model_aliases['OPENAI'], 1):
                        print(f"{Colors.CYAN}[{i}] {Colors.GREEN}{model}{Colors.ENDC}")
                    
                    model_choice = input("Select default model (1-4) or press Enter for gpt-4o: ").strip()
                    if not model_choice:
                        model_info['default_model'] = 'openai/gpt-4o'
                    elif model_choice.isdigit() and 1 <= int(model_choice) <= len(model_aliases['OPENAI']):
                        model_info['default_model'] = model_aliases['OPENAI'][int(model_choice) - 1]
                    else:
                        model_info['default_model'] = 'openai/gpt-4o'
                        print_warning(f"Invalid selection, using default: openai/gpt-4o")
                    break
                print_error("Invalid API key format. It should be at least 10 characters long.")
        
        elif provider == 'ANTHROPIC':
            while True:
                api_key = input("Enter your Anthropic API key: ")
                if validate_api_key(api_key):
                    api_keys['ANTHROPIC_API_KEY'] = api_key
                    
                    # Recommend default model
                    print(f"\n{Colors.CYAN}Recommended Anthropic models:{Colors.ENDC}")
                    for i, model in enumerate(model_aliases['ANTHROPIC'], 1):
                        print(f"{Colors.CYAN}[{i}] {Colors.GREEN}{model}{Colors.ENDC}")
                    
                    model_choice = input("Select default model (1-3) or press Enter for claude-3-7-sonnet: ").strip()
                    if not model_choice or model_choice == '1':
                        model_info['default_model'] = 'anthropic/claude-3-7-sonnet-latest'
                    elif model_choice.isdigit() and 1 <= int(model_choice) <= len(model_aliases['ANTHROPIC']):
                        model_info['default_model'] = model_aliases['ANTHROPIC'][int(model_choice) - 1]
                    else:
                        model_info['default_model'] = 'anthropic/claude-3-7-sonnet-latest'
                        print_warning(f"Invalid selection, using default: anthropic/claude-3-7-sonnet-latest")
                    break
                print_error("Invalid API key format. It should be at least 10 characters long.")
        
        elif provider == 'OPENROUTER':
            while True:
                api_key = input("Enter your OpenRouter API key: ")
                if validate_api_key(api_key):
                    api_keys['OPENROUTER_API_KEY'] = api_key
                    api_keys['OPENROUTER_API_BASE'] = 'https://openrouter.ai/api/v1'

                    # Recommend default model
                    print(f"\n{Colors.CYAN}Recommended OpenRouter models:{Colors.ENDC}")
                    for i, model in enumerate(model_aliases['OPENROUTER'], 1):
                        print(f"{Colors.CYAN}[{i}] {Colors.GREEN}{model}{Colors.ENDC}")
                    
                    model_choice = input("Select default model (1-3) or press Enter for gemini-2.5-flash: ").strip()
                    if not model_choice or model_choice == '1':
                        model_info['default_model'] = 'openrouter/google/gemini-2.5-flash-preview'
                    elif model_choice.isdigit() and 1 <= int(model_choice) <= len(model_aliases['OPENROUTER']):
                        model_info['default_model'] = model_aliases['OPENROUTER'][int(model_choice) - 1]
                    else:
                        model_info['default_model'] = 'openrouter/google/gemini-2.5-flash-preview'
                        print_warning(f"Invalid selection, using default: openrouter/google/gemini-2.5-flash-preview")
                    break
                print_error("Invalid API key format. It should be at least 10 characters long.")
        
    # If no default model has been set, check which provider was selected and set an appropriate default
    if 'default_model' not in model_info:
        if 'ANTHROPIC_API_KEY' in api_keys:
            model_info['default_model'] = 'anthropic/claude-3-7-sonnet-latest'
        elif 'OPENAI_API_KEY' in api_keys:
            model_info['default_model'] = 'openai/gpt-4o'
        elif 'OPENROUTER_API_KEY' in api_keys:
            model_info['default_model'] = 'openrouter/google/gemini-2.5-flash-preview'
    
    print_success(f"Using {model_info['default_model']} as the default model")
    
    # Add the default model to the API keys dictionary
    api_keys['MODEL_TO_USE'] = model_info['default_model']
    
    return api_keys

def collect_search_api_keys():
    """Collect search API keys (now required, not optional)"""
    print_info("You'll need to obtain API keys for search and web scraping")
    print_info("Visit https://tavily.com/ to get a Tavily API key")
    print_info("Visit https://firecrawl.dev/ to get a Firecrawl API key")
    
    while True:
        tavily_api_key = input("Enter your Tavily API key: ")
        if validate_api_key(tavily_api_key):
            break
        print_error("Invalid API key format. It should be at least 10 characters long.")
    
    while True:
        firecrawl_api_key = input("Enter your Firecrawl API key: ")
        if validate_api_key(firecrawl_api_key):
            break
        print_error("Invalid API key format. It should be at least 10 characters long.")
    
    # Ask if user is self-hosting Firecrawl
    is_self_hosted = input("Are you self-hosting Firecrawl? (y/n): ").lower().strip() == 'y'
    firecrawl_url = "https://api.firecrawl.dev"  # Default URL
    
    if is_self_hosted:
        while True:
            custom_url = input("Enter your Firecrawl URL (e.g., https://your-firecrawl-instance.com): ")
            if validate_url(custom_url):
                firecrawl_url = custom_url
                break
            print_error("Invalid URL format. Please enter a valid URL.")
    
    return {
        'TAVILY_API_KEY': tavily_api_key,
        'FIRECRAWL_API_KEY': firecrawl_api_key,
        'FIRECRAWL_URL': firecrawl_url,
    }

def collect_rapidapi_keys():
    """Collect RapidAPI key (optional)"""
    print_info("To enable API services like LinkedIn, and others, you'll need a RapidAPI key")
    print_info("Each service requires individual activation in your RapidAPI account:")
    print_info("1. Locate the service's `base_url` in its corresponding file (e.g., https://linkedin-data-scraper.p.rapidapi.com in backend/agent/tools/data_providers/LinkedinProvider.py)")
    print_info("2. Visit that specific API on the RapidAPI marketplace")
    print_info("3. Subscribe to th`e service (many offer free tiers with limited requests)")
    print_info("4. Once subscribed, the service will be available to your agent through the API Services tool")
    print_info("A RapidAPI key is optional for API services like LinkedIn")
    print_info("Visit https://rapidapi.com/ to get your API key if needed")
    print_info("You can leave this blank and add it later if desired")
    
    rapid_api_key = input("Enter your RapidAPI key (optional, press Enter to skip): ")
    
    # Allow empty key
    if not rapid_api_key:
        print_info("Skipping RapidAPI key setup. You can add it later if needed.")
    else:
        # Validate if not empty
        if not validate_api_key(rapid_api_key, allow_empty=True):
            print_warning("The API key format seems invalid, but continuing anyway.")
    
    return {
        'RAPID_API_KEY': rapid_api_key,
    }

def configure_backend_env(env_vars, use_docker=True):
    """Configure backend .env file"""
    env_path = os.path.join('backend', '.env')
    
    # Redis configuration (based on deployment method)
    redis_host = 'redis' if use_docker else 'localhost'
    redis_config = {
        'REDIS_HOST': redis_host,
        'REDIS_PORT': '6379',
        'REDIS_PASSWORD': '',
        'REDIS_SSL': 'false',
    }

    # RabbitMQ configuration (based on deployment method)
    rabbitmq_host = 'rabbitmq' if use_docker else 'localhost'
    rabbitmq_config = {
        'RABBITMQ_HOST': rabbitmq_host,
        'RABBITMQ_PORT': '5672',
    }
    
    # Organize all configuration
    all_config = {}
    
    # Create a string with the formatted content
    env_content = """# Generated by Suna setup script

# Environment Mode
# Valid values: local, staging, production
ENV_MODE=local

#DATABASE
"""

    # Supabase section
    for key, value in env_vars['supabase'].items():
        env_content += f"{key}={value}\n"
    
    # Redis section
    env_content += "\n# REDIS\n"
    for key, value in redis_config.items():
        env_content += f"{key}={value}\n"
    
    # RabbitMQ section
    env_content += "\n# RABBITMQ\n"
    for key, value in rabbitmq_config.items():
        env_content += f"{key}={value}\n"
    
    # LLM section
    env_content += "\n# LLM Providers:\n"
    # Add empty values for all LLM providers we support
    all_llm_keys = ['ANTHROPIC_API_KEY', 'OPENAI_API_KEY', 'GROQ_API_KEY', 'OPENROUTER_API_KEY', 'MODEL_TO_USE']
    # Add AWS keys separately
    aws_keys = ['AWS_ACCESS_KEY_ID', 'AWS_SECRET_ACCESS_KEY', 'AWS_REGION_NAME']
    
    # First add the keys that were provided
    for key, value in env_vars['llm'].items():
        if key in all_llm_keys:
            env_content += f"{key}={value}\n"
            # Remove from the list once added
            if key in all_llm_keys:
                all_llm_keys.remove(key)
    
    # Add empty values for any remaining LLM keys
    for key in all_llm_keys:
        env_content += f"{key}=\n"
    
    # AWS section
    env_content += "\n# AWS Bedrock\n"
    for key in aws_keys:
        value = env_vars['llm'].get(key, '')
        env_content += f"{key}={value}\n"
    
    # Additional OpenRouter params
    if 'OR_SITE_URL' in env_vars['llm'] or 'OR_APP_NAME' in env_vars['llm']:
        env_content += "\n# OpenRouter Additional Settings\n"
        if 'OR_SITE_URL' in env_vars['llm']:
            env_content += f"OR_SITE_URL={env_vars['llm']['OR_SITE_URL']}\n"
        if 'OR_APP_NAME' in env_vars['llm']:
            env_content += f"OR_APP_NAME={env_vars['llm']['OR_APP_NAME']}\n"
    
    # DATA APIs section
    env_content += "\n# DATA APIS\n"
    for key, value in env_vars['rapidapi'].items():
        env_content += f"{key}={value}\n"
    
    # Web search section
    env_content += "\n# WEB SEARCH\n"
    tavily_key = env_vars['search'].get('TAVILY_API_KEY', '')
    env_content += f"TAVILY_API_KEY={tavily_key}\n"
    
    # Web scrape section
    env_content += "\n# WEB SCRAPE\n"
    firecrawl_key = env_vars['search'].get('FIRECRAWL_API_KEY', '')
    firecrawl_url = env_vars['search'].get('FIRECRAWL_URL', '')
    env_content += f"FIRECRAWL_API_KEY={firecrawl_key}\n"
    env_content += f"FIRECRAWL_URL={firecrawl_url}\n"
    
    # Daytona section
    env_content += "\n# Sandbox container provider:\n"
    for key, value in env_vars['daytona'].items():
        env_content += f"{key}={value}\n"
    
    # Add next public URL at the end
    env_content += f"NEXT_PUBLIC_URL=http://localhost:3000\n"
    
    # Write to file
    with open(env_path, 'w') as f:
        f.write(env_content)
    
    print_success(f"Backend .env file created at {env_path}")
    print_info(f"Redis host is set to: {redis_host}")
    print_info(f"RabbitMQ host is set to: {rabbitmq_host}")

def configure_frontend_env(env_vars, use_docker=True):
    """Configure frontend .env.local file"""
    env_path = os.path.join('frontend', '.env.local')
    
    # Use the appropriate backend URL based on start method
    backend_url = "http://localhost:8000/api"

    config = {
        'NEXT_PUBLIC_SUPABASE_URL': env_vars['supabase']['SUPABASE_URL'],
        'NEXT_PUBLIC_SUPABASE_ANON_KEY': env_vars['supabase']['SUPABASE_ANON_KEY'],
        'NEXT_PUBLIC_BACKEND_URL': backend_url,
        'NEXT_PUBLIC_URL': 'http://localhost:3000',
        'NEXT_PUBLIC_ENV_MODE': 'LOCAL',
    }

    # Write to file
    with open(env_path, 'w') as f:
        for key, value in config.items():
            f.write(f"{key}={value}\n")
    
    print_success(f"Frontend .env.local file created at {env_path}")
    print_info(f"Backend URL is set to: {backend_url}")

def setup_supabase():
    """Setup Supabase database"""
    print_info("Setting up Supabase database...")
    
    # Check if the Supabase CLI is installed
    try:
        subprocess.run(
            ['supabase', '--version'],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=True,
            shell=IS_WINDOWS
        )
        print_success("Supabase CLI is installed.")
    except (subprocess.SubprocessError, FileNotFoundError):
        print_error("Supabase CLI is not initially detected.")
        if IS_WINDOWS:
            print_info("Attempting to install Supabase CLI globally using npm...")
            try:
                # Check if npm is installed (as it's a prerequisite for this step)
                subprocess.run(['npm', '--version'], stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True, shell=IS_WINDOWS)
                
                # Attempt to install Supabase CLI via npm
                npm_install_command = ['npm', 'install', '-g', 'supabase']
                print_info(f"Running command: {' '.join(npm_install_command)}")
                npm_install_process = subprocess.run(
                    npm_install_command,
                    capture_output=True, text=True, check=True, shell=IS_WINDOWS
                )
                print_info("npm install stdout:\n" + npm_install_process.stdout)
                if npm_install_process.stderr:
                    print_warning("npm install stderr:\n" + npm_install_process.stderr) # Some warnings might not be fatal

                print_success("Supabase CLI installed successfully via npm.")
                
                # Re-verify installation
                try:
                    subprocess.run(
                        ['supabase', '--version'],
                        stdout=subprocess.PIPE,
                        stderr=subprocess.PIPE,
                        check=True,
                        shell=IS_WINDOWS
                    )
                    print_success("Supabase CLI successfully verified after npm installation.")
                    # If successful, we can proceed with the rest of setup_supabase()
                except (subprocess.SubprocessError, FileNotFoundError):
                    print_warning("Supabase CLI was reportedly installed by npm, but 'supabase --version' still fails.")
                    print_info("This is often a PATH issue. Please open a new terminal/command prompt and re-run this setup script.")
                    print_info("If the issue persists, you may need to manually add the global npm packages directory to your PATH or follow the manual Supabase CLI installation instructions.")
                    print_info("Manual installation instructions: https://supabase.com/docs/guides/cli/getting-started")
                    sys.exit(1) # Exit because Supabase CLI is critical at this stage

            except FileNotFoundError: # If npm itself is not found
                 print_error("npm command not found. Cannot attempt Supabase CLI installation via npm.")
                 print_info("Please ensure Node.js and npm are correctly installed and in your PATH (this should have been checked in 'check_requirements').")
                 print_info("Then, either re-run this script or install Supabase CLI manually by following instructions at https://supabase.com/docs/guides/cli/getting-started")
                 sys.exit(1)
            except subprocess.SubprocessError as e:
                print_error(f"Failed to install Supabase CLI via npm: {e}")
                if hasattr(e, 'stdout') and e.stdout:
                    print_error("npm install stdout:\n" + e.stdout)
                if hasattr(e, 'stderr') and e.stderr:
                    print_error("npm install stderr:\n" + e.stderr)
                print_info("Please install Supabase CLI manually by following instructions at https://supabase.com/docs/guides/cli/getting-started")
                print_info("After installing, run this setup again.")
                sys.exit(1)
        else: # Not Windows, or npm attempt was skipped/failed previously
            print_info("Please install Supabase CLI manually by following instructions at https://supabase.com/docs/guides/cli/getting-started")
            print_info("After installing, run this setup again.")
            sys.exit(1)
            
    # Extract project reference from Supabase URL
    supabase_url = os.environ.get('SUPABASE_URL')
    if not supabase_url:
        # Get from main function if environment variable not set
        env_path = os.path.join('backend', '.env')
        if os.path.exists(env_path):
            with open(env_path, 'r') as f:
                for line in f:
                    if line.startswith('SUPABASE_URL='):
                        supabase_url = line.strip().split('=', 1)[1]
                        break

    project_ref = None
    if supabase_url:
        # Extract project reference from URL (format: https://[project_ref].supabase.co)
        match = re.search(r'https://([^.]+)\.supabase\.co', supabase_url)
        if match:
            project_ref = match.group(1)
            print_success(f"Extracted project reference '{project_ref}' from your Supabase URL")
    
    # If extraction failed, ask the user
    if not project_ref:
        print_info("Could not extract project reference from Supabase URL")
        print_info("Get your Supabase project reference from the Supabase dashboard")
        print_info("It's the portion after 'https://' and before '.supabase.co' in your project URL")
        project_ref = input("Enter your Supabase project reference: ")
    
    # Change the working directory to backend
    backend_dir = os.path.join(os.getcwd(), 'backend')
    print_info(f"Changing to backend directory: {backend_dir}")
    
    try:
        # Login to Supabase CLI (interactive)
        print_info("Logging into Supabase CLI...")
        subprocess.run(['supabase', 'login'], check=True, shell=IS_WINDOWS)
        
        # Link to project
        print_info(f"Linking to Supabase project {project_ref}...")
        subprocess.run(
            ['supabase', 'link', '--project-ref', project_ref],
            cwd=backend_dir,
            check=True,
            shell=IS_WINDOWS
        )
        
        # Push database migrations
        print_info("Pushing database migrations...")
        subprocess.run(
            ['supabase', 'db', 'push'],
            cwd=backend_dir,
            check=True,
            shell=IS_WINDOWS
        )
        
        print_success("Supabase database setup completed")
        
        # Reminder for manual step
        print_warning("IMPORTANT: You need to manually expose the 'basejump' schema in Supabase")
        print_info("Go to the Supabase web platform -> choose your project -> Project Settings -> Data API")
        print_info("In the 'Exposed Schema' section, add 'basejump' if not already there")
        input("Press Enter once you've completed this step...")
        
    except subprocess.SubprocessError as e:
        print_error(f"Failed to setup Supabase: {e}")
        sys.exit(1)

def install_dependencies():
    """Install frontend and backend dependencies"""
    print_info("Installing required dependencies...")
    
    try:
        # Install frontend dependencies
        print_info("Installing frontend dependencies...")
        subprocess.run(
            ['npm', 'install'], 
            cwd='frontend',
            check=True,
            shell=IS_WINDOWS
        )
        print_success("Frontend dependencies installed successfully")
        
        # Lock dependencies
        print_info("Locking backend dependencies using poetry...")
        poetry_base_command = [sys.executable, '-m', 'poetry'] if os.environ.get(VENV_ACTIVATION_MARKER) == "1" else ['poetry']
        
        lock_command = poetry_base_command + ['lock', '--no-update'] # Use --no-update to respect pyproject.toml versions unless strictly necessary
        print_info(f"Executing: {' '.join(lock_command)} in backend directory")
        try:
            subprocess.run(
                lock_command,
                cwd='backend',
                check=True,
                shell=IS_WINDOWS # shell=True if 'poetry' might be a .bat or .cmd on Windows not directly executable
            )
            print_success("Poetry lock successful.")
        except subprocess.SubprocessError as e_lock:
            print_error(f"Poetry lock failed: {e_lock}")
            # Try direct poetry if sys.executable -m poetry failed and we weren't using direct poetry already
            if poetry_base_command[0] == sys.executable:
                print_info("Retrying poetry lock with direct 'poetry' command...")
                try:
                    subprocess.run(['poetry', 'lock', '--no-update'], cwd='backend', check=True, shell=IS_WINDOWS)
                    print_success("Poetry lock successful with direct command.")
                except subprocess.SubprocessError as e_lock_direct:
                    print_error(f"Direct poetry lock also failed: {e_lock_direct}")
                    print_info("Please ensure Poetry is installed and accessible. If you installed it via pip in the venv, it might not be in PATH.")
                    print_info("You might need to activate the venv manually or add Poetry's script directory to PATH.")
                    return False # Exit install_dependencies due to failure
            else: # Direct poetry already failed
                 print_info("Please ensure Poetry is installed and accessible.")
                 return False


        # Install backend dependencies
        print_info("Installing backend dependencies using poetry...")
        install_command = poetry_base_command + ['install']
        print_info(f"Executing: {' '.join(install_command)} in backend directory")
        try:
            subprocess.run(
                install_command, 
                cwd='backend',
                check=True,
                shell=IS_WINDOWS
            )
            print_success("Backend dependencies installed successfully.")
        except subprocess.SubprocessError as e_install:
            print_error(f"Poetry install failed: {e_install}")
            if poetry_base_command[0] == sys.executable:
                print_info("Retrying poetry install with direct 'poetry' command...")
                try:
                    subprocess.run(['poetry', 'install'], cwd='backend', check=True, shell=IS_WINDOWS)
                    print_success("Poetry install successful with direct command.")
                except subprocess.SubprocessError as e_install_direct:
                    print_error(f"Direct poetry install also failed: {e_install_direct}")
                    print_info("Please ensure Poetry is installed and accessible.")
                    return False
            else:
                print_info("Please ensure Poetry is installed and accessible.")
                return False
            
        print_success("Backend dependencies installed successfully")
        
        return True
    except subprocess.SubprocessError as e:
        print_error(f"Failed to install dependencies: {e}")
        print_info("You may need to install them manually.")
        return False

def start_suna():
    """Start Suna using Docker Compose or manual startup"""
    print_info("You can start Suna using either Docker Compose or by manually starting the frontend, backend and worker.")

    print(f"\n{Colors.CYAN}How would you like to start Suna?{Colors.ENDC}")
    print(f"{Colors.CYAN}[1] {Colors.GREEN}Docker Compose{Colors.ENDC} {Colors.CYAN}(recommended, starts all services){Colors.ENDC}")
    print(f"{Colors.CYAN}[2] {Colors.GREEN}Manual startup{Colors.ENDC} {Colors.CYAN}(requires Redis, RabbitMQ & separate terminals){Colors.ENDC}\n")
    
    while True:
        start_method = input("Enter your choice (1 or 2): ")
        if start_method in ["1", "2"]:
            break
        print_error("Invalid selection. Please enter '1' for Docker Compose or '2' for Manual startup.")
    
    use_docker = start_method == "1"
    
    if use_docker:
        print_info("Starting Suna with Docker Compose...")
        
        try:
            # TODO: uncomment when we have pre-built images on Docker Hub or GHCR
            # GitHub repository environment variable setup
            # github_repo = None
            
            # print(f"\n{Colors.CYAN}Do you want to use pre-built images or build locally?{Colors.ENDC}")
            # print(f"{Colors.CYAN}[1] {Colors.GREEN}Pre-built images{Colors.ENDC} {Colors.CYAN}(faster){Colors.ENDC}")
            # print(f"{Colors.CYAN}[2] {Colors.GREEN}Build locally{Colors.ENDC} {Colors.CYAN}(customizable){Colors.ENDC}\n")
            
            # while True:
            #     build_choice = input("Enter your choice (1 or 2): ")
            #     if build_choice in ["1", "2"]:
            #         break
            #     print_error("Invalid selection. Please enter '1' for pre-built images or '2' for building locally.")
                
            # use_prebuilt = build_choice == "1"
            
            # if use_prebuilt:
            #     # Get GitHub repository name from user
            #     print_info("For pre-built images, you need to specify a GitHub repository name")
            #     print_info("Example format: your-github-username/repo-name")
                
            #     github_repo = input("Enter GitHub repository name: ")
            #     if not github_repo or "/" not in github_repo:
            #         print_warning("Invalid GitHub repository format. Using a default value.")
            #         # Create a random GitHub repository name as fallback
            #         random_name = ''.join(random.choices(string.ascii_lowercase, k=8))
            #         github_repo = f"user/{random_name}"
                
            #     # Set the environment variable
            #     os.environ["GITHUB_REPOSITORY"] = github_repo
            #     print_info(f"Using GitHub repository: {github_repo}")
                
            #     # Start with pre-built images
            #     print_info("Using pre-built images...")
            #     subprocess.run(['docker', 'compose', '-f', 'docker-compose.ghcr.yaml', 'up', '-d'], check=True)
            # else:
            #     # Start with docker-compose (build images locally)
            #     print_info("Building images locally...")
            #     subprocess.run(['docker', 'compose', 'up', '-d'], check=True)

            print_info("Building images locally...")
            subprocess.run(['docker', 'compose', 'up', '-d', '--build'], check=True, shell=IS_WINDOWS)

            # Wait for services to be ready
            print_info("Waiting for services to start...")
            time.sleep(10)  # Give services some time to start
            
            # Check if services are running
            result = subprocess.run(
                ['docker', 'compose', 'ps', '-q'],
                capture_output=True,
                text=True,
                shell=IS_WINDOWS
            )
            
            if "backend" in result.stdout and "frontend" in result.stdout:
                print_success("Suna services are up and running!")
            else:
                print_warning("Some services might not be running correctly. Check 'docker compose ps' for details.")
            
        except subprocess.SubprocessError as e:
            print_error(f"Failed to start Suna: {e}")
            sys.exit(1)
            
        return use_docker
    else:
        print_info("For manual startup, you'll need to:")
        print_info("1. Start Redis and RabbitMQ in Docker (required for the backend)")
        print_info("2. Start the frontend with npm run dev")
        print_info("3. Start the backend with poetry run python3.11 api.py")
        print_info("4. Start the worker with poetry run python3.11 -m dramatiq run_agent_background")
        print_warning("Note: Redis and RabbitMQ must be running before starting the backend")
        print_info("Detailed instructions will be provided at the end of setup")
        
        return use_docker

def final_instructions(use_docker=True, env_vars=None):
    """Show final instructions"""
    print(f"\n{Colors.GREEN}{Colors.BOLD}✨ Suna Setup Complete! ✨{Colors.ENDC}\n")
    
    # Display LLM configuration info if available
    if env_vars and 'llm' in env_vars and 'MODEL_TO_USE' in env_vars['llm']:
        default_model = env_vars['llm']['MODEL_TO_USE']
        print_info(f"Suna is configured to use {Colors.GREEN}{default_model}{Colors.ENDC} as the default LLM model")

    if use_docker:
        print_info("Your Suna instance is now running!")
        print_info("Access it at: http://localhost:3000")
        print_info("Create an account using Supabase authentication to start using Suna")
        print("\nUseful Docker commands:")
        print(f"{Colors.CYAN}  docker compose ps{Colors.ENDC}         - Check the status of Suna services")
        print(f"{Colors.CYAN}  docker compose logs{Colors.ENDC}       - View logs from all services")
        print(f"{Colors.CYAN}  docker compose logs -f{Colors.ENDC}    - Follow logs from all services")
        print(f"{Colors.CYAN}  docker compose down{Colors.ENDC}       - Stop Suna services")
        print(f"{Colors.CYAN}  docker compose up -d{Colors.ENDC}      - Start Suna services (after they've been stopped)")
    else:
        print_info("Suna setup is complete but services are not running yet.")
        print_info("To start Suna, you need to:")
        
        print_info("1. Start Redis and RabbitMQ (required for backend):")
        print(f"{Colors.CYAN}    cd backend")
        print(f"    docker compose up redis rabbitmq -d{Colors.ENDC}")
        
        print_info("2. In one terminal:")
        print(f"{Colors.CYAN}    cd frontend")
        print(f"    npm run dev{Colors.ENDC}")
        
        print_info("3. In another terminal:")
        print(f"{Colors.CYAN}    cd backend")
        print(f"    poetry run python3.11 api.py{Colors.ENDC}")
        
        print_info("3. In one more terminal:")
        print(f"{Colors.CYAN}    cd backend")
        print(f"    poetry run python3.11 -m dramatiq run_agent_background{Colors.ENDC}")
        
        print_info("4. Once all services are running, access Suna at: http://localhost:3000")
        print_info("5. Create an account using Supabase authentication to start using Suna")

# Path to the virtual environment
VENV_PATH = os.path.join(os.getcwd(), '.venv')
PYTHON_IN_VENV = os.path.join(VENV_PATH, 'Scripts', 'python.exe') if IS_WINDOWS else os.path.join(VENV_PATH, 'bin', 'python')
VENV_ACTIVATION_MARKER = "_SUNA_SETUP_IN_VENV_" # Environment variable to prevent re-launch loop

def get_python_version(python_exe='python'):
    """Gets the version of the specified python executable."""
    try:
        process = subprocess.run([python_exe, '--version'], capture_output=True, text=True, check=True, shell=IS_WINDOWS)
        version_output = process.stdout.strip() + process.stderr.strip() # stderr for some python versions like system python on mac
        match = re.search(r"Python (\d+\.\d+\.\d+)", version_output)
        if match:
            return match.group(1)
        return None
    except (subprocess.SubprocessError, FileNotFoundError):
        return None

def ensure_python_311_and_venv():
    """
    Ensures Python 3.11 is available and the script is running in a venv with it.
    If not, it attempts to install Python 3.11 (on Windows), create the venv,
    and re-launch the setup script from within the venv.
    """
    if os.environ.get(VENV_ACTIVATION_MARKER) == "1":
        print_info("Already running in the Suna setup virtual environment.")
        # Verify it's actually Python 3.11 in the venv
        current_version = get_python_version(sys.executable)
        if current_version and "3.11" in current_version:
            print_success(f"Confirmed Python {current_version} in venv.")
            return # Already in the correct venv
        else:
            print_error(f"Running in a venv, but it's Python {current_version}, not 3.11. Please delete the .venv folder and re-run.")
            sys.exit(1)

    print_info("Checking Python 3.11 and virtual environment...")

    # 1. Check if system Python is 3.11
    system_python_executable = "python" # Default
    python_version = get_python_version(system_python_executable)
    if not (python_version and "3.11" in python_version):
        if IS_WINDOWS:
            # Try 'py -3.11' if 'python' isn't 3.11
            python_version = get_python_version('py -3.11')
            if python_version and "3.11" in python_version:
                system_python_executable = 'py -3.11' # Found a way to call 3.11
                print_success(f"Found Python {python_version} using '{system_python_executable}'.")
            else: # 'python' and 'py -3.11' are not 3.11
                print_warning("System Python is not 3.11. Attempting to install Python 3.11 using winget...")
                success, already_installed = run_winget_install("Python.Python.3.11", "Python 3.11")
                if success:
                    if already_installed:
                        print_info("Python 3.11 was already installed (as reported by winget).")
                    else:
                        print_success("Python 3.11 installed successfully via winget.")
                    print_info("Please re-run this script in a new terminal for changes to take effect and for Python 3.11 to be detected.")
                    sys.exit(0) # Exit for user to re-run in new terminal
                else:
                    print_error("Failed to install Python 3.11 using winget.")
                    print_info("Please install Python 3.11 manually from https://www.python.org/downloads/ and ensure it's in your PATH.")
                    sys.exit(1)
        else: # Not Windows
            print_error("Python 3.11 is required but not found in your PATH.")
            print_info("Please install Python 3.11 manually from https://www.python.org/downloads/ or use your system's package manager.")
            sys.exit(1)
    else: # 'python' command is already 3.11
        print_success(f"Found Python {python_version} using '{system_python_executable}'.")


    # 2. Create or verify the virtual environment
    if not os.path.exists(VENV_PATH):
        print_info(f"Creating virtual environment at: {VENV_PATH} using {system_python_executable}")
        try:
            # Use the confirmed Python 3.11 executable to create the venv
            subprocess.run([system_python_executable, '-m', 'venv', VENV_PATH], check=True, shell=IS_WINDOWS)
            print_success("Virtual environment created successfully.")
        except subprocess.SubprocessError as e:
            print_error(f"Failed to create virtual environment: {e}")
            sys.exit(1)
    else: # VENV_PATH exists
        print_info(f"Virtual environment directory '{VENV_PATH}' already exists.")
        # Check if the Python in this venv is 3.11
        venv_python_version = get_python_version(PYTHON_IN_VENV)
        if venv_python_version and "3.11" in venv_python_version:
            print_success(f"Existing venv uses Python {venv_python_version}.")
        else:
            print_warning(f"Existing venv at '{VENV_PATH}' does not seem to use Python 3.11 (found {venv_python_version}).")
            recreate_venv = input(f"{Colors.YELLOW}Do you want to remove the existing .venv and recreate it? (yes/no): {Colors.ENDC}").strip().lower()
            if recreate_venv in ['yes', 'y']:
                try:
                    import shutil
                    shutil.rmtree(VENV_PATH)
                    print_info(f"Removed existing .venv directory.")
                    print_info(f"Creating virtual environment at: {VENV_PATH} using {system_python_executable}")
                    subprocess.run([system_python_executable, '-m', 'venv', VENV_PATH], check=True, shell=IS_WINDOWS)
                    print_success("Virtual environment recreated successfully.")
                except Exception as e:
                    print_error(f"Failed to recreate virtual environment: {e}. Please remove '.venv' manually and re-run.")
                    sys.exit(1)
            else:
                print_info("Proceeding with existing .venv. If issues occur, please remove it manually and re-run.")

    # 3. Re-launch script from venv if not already in it
    print_info(f"Checking if running from venv: sys.prefix='{sys.prefix}', VENV_PATH='{os.path.abspath(VENV_PATH)}'")
    # More robust check for venv activation:
    # On Windows, sys.prefix for a venv is the venv path itself.
    # On Unix, sys.prefix for a venv is also the venv path.
    # sys.base_prefix points to the original Python installation.
    # If they are different, we are in a venv.
    is_in_venv = sys.prefix != sys.base_prefix 
    
    # Additionally, check if the venv is the one we created/expect
    expected_venv_path_abs = os.path.abspath(VENV_PATH)
    current_venv_path_abs = os.path.abspath(sys.prefix)

    if is_in_venv and current_venv_path_abs == expected_venv_path_abs:
        print_success(f"Correct virtual environment ('{expected_venv_path_abs}') is already active.")
        # Set marker for subsequent checks within the same run (e.g. if ensure_python_311_and_venv is called again)
        os.environ[VENV_ACTIVATION_MARKER] = "1"
        # And confirm it's 3.11
        current_version_in_venv = get_python_version(sys.executable)
        if current_version_in_venv and "3.11" in current_version_in_venv:
             print_success(f"Confirmed Python {current_version_in_venv} in active venv.")
             return # All good
        else:
            print_error(f"Script is in the correct venv path, but Python version is {current_version_in_venv}, not 3.11. This is unexpected.")
            print_info("Please delete the .venv folder and re-run the script.")
            sys.exit(1)
    else:
        if is_in_venv:
            print_warning(f"Script is running in a virtual environment ('{current_venv_path_abs}'), but not the expected one ('{expected_venv_path_abs}').")
            print_info("Attempting to re-launch in the correct Suna virtual environment...")
        else:
             print_info("Not running in the Suna virtual environment. Attempting to re-launch...")

        print_info(f"Re-launching setup with Python from: {PYTHON_IN_VENV}")
        
        # Set the marker environment variable before re-launching
        os.environ[VENV_ACTIVATION_MARKER] = "1"
        
        try:
            # sys.argv includes the script name as the first argument.
            # We want to run 'python.exe setup.py install' (or other args)
            args_for_subprocess = [PYTHON_IN_VENV] + sys.argv
            print_info(f"Executing: {' '.join(args_for_subprocess)}")
            
            # For Windows, shell=True might sometimes be needed if PYTHON_IN_VENV has spaces
            # and we are not careful with quoting, but subprocess typically handles this.
            # Pass current environment variables, including the marker.
            process = subprocess.Popen(args_for_subprocess, env=os.environ.copy())
            process.wait() # Wait for the new process to complete
            sys.exit(process.returncode) # Exit with the same code as the child process

        except FileNotFoundError:
            print_error(f"Failed to re-launch: Python executable not found at {PYTHON_IN_VENV}")
            print_info("Ensure the virtual environment was created correctly.")
            sys.exit(1)
        except subprocess.SubprocessError as e:
            print_error(f"Failed to re-launch script in virtual environment: {e}")
            sys.exit(1)

def main():
    # Ensure Python 3.11 and venv are set up before anything else.
    # This function will handle re-launching if necessary.
    ensure_python_311_and_venv()

    total_steps = 8
    current_step = 1
    
    print_banner()
    print("This wizard will guide you through setting up Suna, an open-source generalist AI agent.\n")
    
    print_step(current_step, total_steps, "Checking requirements & Environment")
    # check_requirements() will be called, and Python 3.11 check within it should now pass
    # because we are (or will be after re-launch) in the venv.
    check_requirements() # Python check within this should be fine now
    check_docker_running()
    
    if not check_suna_directory():
        print_error("This setup script must be run from the Suna repository root directory.")
        sys.exit(1)
    current_step += 1
    
    # Steps below assume we are now running in the correct Python 3.11 venv
    print_step(current_step, total_steps, "Collecting Supabase information")
    supabase_info = collect_supabase_info()
    # Set Supabase URL in environment for later use
    os.environ['SUPABASE_URL'] = supabase_info['SUPABASE_URL']
    current_step += 1
    
    print_step(current_step, total_steps, "Collecting Daytona information")
    daytona_info = collect_daytona_info()
    current_step += 1
    
    print_step(current_step, total_steps, "Collecting LLM API keys")
    llm_api_keys = collect_llm_api_keys()
    current_step += 1
    
    print_step(current_step, total_steps, "Collecting search and web scraping API keys")
    search_api_keys = collect_search_api_keys()
    current_step += 1
    
    print_step(current_step, total_steps, "Collecting RapidAPI key")
    rapidapi_keys = collect_rapidapi_keys()
    current_step += 1
    
    # Combine all environment variables
    env_vars = {
        'supabase': supabase_info,
        'daytona': daytona_info,
        'llm': llm_api_keys,
        'search': search_api_keys,
        'rapidapi': rapidapi_keys,
    }
    
    # Setup Supabase database
    setup_supabase()
    current_step += 1
    
    # Install dependencies before starting Suna
    print_step(current_step, total_steps, "Installing dependencies")
    install_dependencies()
    
    # Configure environment files with the correct settings before starting
    print_info("Configuring environment files...")
    configure_backend_env(env_vars, True)  # Always create for Docker first
    configure_frontend_env(env_vars, True)
    
    # Now ask how to start Suna
    print_step(current_step, total_steps, "Starting Suna")
    use_docker = start_suna()
    
    # Update environment files if needed for non-Docker setup
    if not use_docker:
        print_info("Updating environment files for manual startup...")
        configure_backend_env(env_vars, use_docker)
        configure_frontend_env(env_vars, use_docker)
    
    # Final instructions
    final_instructions(use_docker, env_vars)

if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n\nSetup interrupted. You can resume setup anytime by running this script again.")
        sys.exit(1)
