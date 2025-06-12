#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
AI-Powered Git Commit Script

This script uses the Gemini AI to analyze staged file changes,
group them into logical commits, and generate conventional commit messages.

Author: Gemini
Date: 2025-06-07

Prerequisites:
- Python 3.6+
- Git installed on your system.
- An Google AI (Gemini) API key.

Setup:
1. Install the required Python library:
   pip install -q -U google-generativeai

2. Set your API key as an environment variable:
   export GEMINI_API_KEY="YOUR_API_KEY"

How to Use:
1. Stage the files you want the AI to analyze:
   git add file1.py file2.md ...
   OR
   git add .

2. Run the script from the root of your git repository:
   python ./ai_git_commit.py --repo-path='../../upgrade/rhcert-spa'
"""

import os
import sys
import subprocess
import json
import google.generativeai as genai # type: ignore

# --- Configuration ---

# ANSI color codes for better terminal output
class colors:
    HEADER = '\033[95m'
    OKBLUE = '\033[94m'
    OKCYAN = '\033[96m'
    OKGREEN = '\033[92m'
    WARNING = '\033[93m'
    FAIL = '\033[91m'
    ENDC = '\033[0m'
    BOLD = '\033[1m'
    UNDERLINE = '\033[4m'

# The AI model to use for generation
AI_MODEL = 'gemini-1.5-flash'

# --- Core Functions ---

def check_prerequisites():
    """Checks if Git is installed and the API key is set."""
    print(f"{colors.OKCYAN}Checking prerequisites...{colors.ENDC}")
    # Check for Git
    try:
        subprocess.run(["git", "--version"], check=True, capture_output=True)
    except (subprocess.CalledProcessError, FileNotFoundError):
        print(f"{colors.FAIL}Error: Git is not installed or not in your PATH.{colors.ENDC}")
        sys.exit(1)

    # Check for API key
    if not os.getenv("GEMINI_API_KEY"):
        print(f"{colors.FAIL}Error: GEMINI_API_KEY environment variable is not set.{colors.ENDC}")
        print(f"{colors.WARNING}Please get a key from Google AI Studio and run 'export GEMINI_API_KEY=\"YOUR_KEY\"'.{colors.ENDC}")
        sys.exit(1)
    
    genai.configure(api_key=os.getenv("GEMINI_API_KEY"))
    print(f"{colors.OKGREEN}Prerequisites met.{colors.ENDC}")

def get_staged_diff():
    """Gets the diff of all staged files."""
    print(f"{colors.OKCYAN}Getting staged file diffs...{colors.ENDC}")
    try:
        # The '--cached' option shows diffs for staged files.
        result = subprocess.run(
            ["git", "diff", "--cached"], 
            check=True, 
            capture_output=True, 
            text=True
        )
        if not result.stdout.strip():
            print(f"{colors.WARNING}No files are staged. Please stage files with 'git add' before running.{colors.ENDC}")
            sys.exit(0)
        return result.stdout
    except subprocess.CalledProcessError as e:
        print(f"{colors.FAIL}Error getting git diff: {e.stderr}{colors.ENDC}")
        sys.exit(1)

def get_ai_suggestions(diff_content):
    """Sends the diff to the AI and gets commit suggestions."""
    print(f"{colors.OKCYAN}Asking AI to analyze changes... (This may take a moment){colors.ENDC}")

    # Prepare the prompt for the AI
    prompt = f"""
    You are an expert programmer and git user. Analyze the following git diff and group the changes into one or more logical commits.
    For each commit, provide a conventional commit message (e.g., "feat: ...", "fix: ...", "chore: ...") and the list of files that belong in that commit.

    The diff is as follows:
    --- DIFF START ---
    {diff_content}
    --- DIFF END ---

    Respond ONLY with a valid JSON object in the following format. Do not include any other text or explanations.
    The format is an array of commit objects:
    [
      {{
        "commit_message": "<conventional_commit_message>",
        "files": ["path/to/file1.py", "path/to/file2.md"]
      }},
      {{
        "commit_message": "<another_commit_message>",
        "files": ["path/to/another/file.css"]
      }}
    ]
    """

    try:
        model = genai.GenerativeModel(AI_MODEL)
        response = model.generate_content(prompt)
        
        # Clean up the response to extract only the JSON part
        json_text = response.text.strip().replace("```json", "").replace("```", "").strip()
        return json.loads(json_text)
    except Exception as e:
        print(f"{colors.FAIL}Error communicating with the AI model: {e}{colors.ENDC}")
        # print(f"Raw AI Response was:\n{response.text if 'response' in locals() else 'N/A'}")
        sys.exit(1)

def process_commits(suggestions):
    """Iterates through AI suggestions and prompts the user to commit."""
    if not suggestions:
        print(f"{colors.WARNING}The AI did not suggest any commits. Nothing to do.{colors.ENDC}")
        return

    print(f"\n{colors.HEADER}{colors.BOLD}AI has suggested {len(suggestions)} commit(s). Reviewing now...{colors.ENDC}")
    print("--------------------------------------------------")

    for i, commit_info in enumerate(suggestions):
        commit_message = commit_info.get("commit_message")
        files_to_commit = commit_info.get("files", [])

        if not commit_message or not files_to_commit:
            print(f"{colors.WARNING}Skipping invalid suggestion: {commit_info}{colors.ENDC}")
            continue

        print(f"\n{colors.BOLD}Suggested Commit {i+1}/{len(suggestions)}:{colors.ENDC}")
        print(f"  {colors.OKGREEN}Message:{colors.ENDC} {commit_message}")
        print(f"  {colors.OKBLUE}Files:{colors.ENDC}")
        for f in files_to_commit:
            print(f"    - {f}")
        
        # Unstage all files first to handle groups correctly
        subprocess.run(["git", "reset"], check=True, capture_output=True)

        # Stage only the files for the current suggested commit
        for f in files_to_commit:
            subprocess.run(["git", "add", f], check=True, capture_output=True)

        user_input = input(f"\n{colors.BOLD}Commit these files with this message? (y/n/e) > {colors.ENDC}").lower()

        if user_input == 'y':
            try:
                subprocess.run(["git", "commit", "-m", commit_message], check=True)
                print(f"{colors.OKGREEN}Commit successful!{colors.ENDC}")
            except subprocess.CalledProcessError as e:
                print(f"{colors.FAIL}Git commit failed: {e.stderr}{colors.ENDC}")
                print(f"{colors.WARNING}Files have been left staged for manual review.{colors.ENDC}")
        elif user_input == 'e':
            edited_message = input("Enter new commit message: ")
            if edited_message:
                subprocess.run(["git", "commit", "-m", edited_message], check=True)
                print(f"{colors.OKGREEN}Commit successful with new message!{colors.ENDC}")
            else:
                 print(f"{colors.WARNING}Empty message. Skipping commit.{colors.ENDC}")
        else:
            print(f"{colors.WARNING}Skipping this commit. Files will be re-staged for the next suggestion if applicable.{colors.ENDC}")

    # After the loop, restage any remaining files that were part of suggestions but not committed
    subprocess.run(["git", "add", "."], check=True, capture_output=True)
    print("\n--------------------------------------------------")
    print(f"{colors.OKGREEN}Review process complete.{colors.ENDC}")


# --- Main Execution ---
if __name__ == "__main__":
    check_prerequisites()
    staged_diff = get_staged_diff()
    ai_suggestions = get_ai_suggestions(staged_diff)
    process_commits(ai_suggestions)
