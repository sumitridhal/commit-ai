#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
AI-Powered Git Commit Script (Local Edition with Fire) - Improved

This script uses a locally hosted Ollama model to analyze file changes,
group them into logical commits, and generate conventional commit messages.

IMPROVEMENTS:
- Phase 1: Automatically detects and commits DELETED files first.
- Phase 2: Automatically detects and commits DEPENDENCY updates (package.json)
- Phase 3: Uses AI to interactively analyze and commit all other changes.

This tiered approach cleans up the working directory and allows the AI to
focus on analyzing new and modified code for more accurate suggestions.
The script will run continuously until all changed files are committed.

Author: Gemini
Date: 2025-06-07

Prerequisites:
- Python 3.6+
- Git installed on your system.
- Ollama installed and running locally.

Setup:
1. Install Ollama for macOS from https://ollama.com/
2. Pull the model you want to use:
   ollama pull mixtral
3. Install dependencies from the updated requirements.txt:
   pip install -r requirements.txt

How to Use:
1. Ensure the Ollama application is running.
2. Run the script, pointing it to your repository. It will loop until all files are committed.
   python ./cli.py commit --repo-path='../../upgrade/rhcert-spa'
"""

import os
import sys
import subprocess
import json
import requests
import fire
import questionary

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

AI_MODEL = 'mistral-nemo'
OLLAMA_API_URL = "http://localhost:11434/api/generate"

# Files to be committed automatically with a generic dependency message
GENERIC_DEPENDENCY_FILES = ['package.json', 'package-lock.json']


class GitAICommitter:
    """A class to handle AI-powered Git commit operations."""

    def _run_git_command(self, command, repo_path):
        """Helper to run a git command in the specified repository path."""
        try:
            return subprocess.run(command, check=True, capture_output=True, text=True, cwd=repo_path)
        except subprocess.CalledProcessError as e:
            print(f"{colors.FAIL}Error running git command '{' '.join(command)}':\n{e.stderr}{colors.ENDC}")
            # Allow some commands (like status on an empty repo) to fail without exiting
            if e.returncode != 1:
                sys.exit(1)
            return None

    def _check_prerequisites(self, repo_path):
        """Checks if Git is installed, Ollama is running, and the path is a git repo."""
        print(f"{colors.OKCYAN}Checking prerequisites...{colors.ENDC}")
        if not self._run_git_command(["git", "--version"], repo_path):
            sys.exit(1)

        git_dir = os.path.join(repo_path, '.git')
        if not os.path.isdir(git_dir):
            print(f"{colors.FAIL}Error: The specified path '{repo_path}' is not a valid git repository.{colors.ENDC}")
            sys.exit(1)

        try:
            requests.get("http://localhost:11434", timeout=3)
        except requests.exceptions.ConnectionError:
            print(f"{colors.FAIL}Error: Could not connect to Ollama server at http://localhost:11434.{colors.ENDC}")
            print(f"{colors.WARNING}Please ensure the Ollama application is running.{colors.ENDC}")
            sys.exit(1)
        
        print(f"{colors.OKGREEN}Prerequisites met.{colors.ENDC}")

    def _get_deleted_files(self, repo_path):
        """Gets a list of unstaged deleted files."""
        result = self._run_git_command(["git", "ls-files", "--deleted"], repo_path)
        if not result or not result.stdout.strip():
            return []
        return result.stdout.strip().split('\n')

    def _commit_deleted_files(self, repo_path, deleted_files):
        """Stages and commits deleted files with a standard message."""
        if not deleted_files:
            return

        print(f"\n{colors.OKBLUE}Found {len(deleted_files)} deleted file(s). Committing them first...{colors.ENDC}")
        for f in deleted_files:
            print(f"  - {colors.WARNING}{f}{colors.ENDC}")
        
        self._run_git_command(["git", "rm"] + deleted_files, repo_path)
        
        commit_message = "refactor(cleanup): remove deleted files"
        self._run_git_command(["git", "commit", "-m", commit_message], repo_path)
        
        print(f"{colors.OKGREEN}Successfully committed deleted files.{colors.ENDC}")

    def _handle_dependency_updates(self, repo_path, changed_files):
        """Finds, stages, and commits dependency file updates automatically."""
        deps_to_commit = [f for f in changed_files if f in GENERIC_DEPENDENCY_FILES]
        
        if not deps_to_commit:
            return []

        print(f"\n{colors.OKBLUE}Found dependency updates. Committing them automatically...{colors.ENDC}")
        for f in deps_to_commit:
            print(f"  - {colors.OKCYAN}{f}{colors.ENDC}")

        self._run_git_command(["git", "add", "--"] + deps_to_commit, repo_path)
        commit_message = "chore(deps): update dependencies"
        self._run_git_command(["git", "commit", "-m", commit_message], repo_path)
        
        print(f"{colors.OKGREEN}Successfully committed dependency files.{colors.ENDC}")
        return deps_to_commit

    def _get_modified_and_new_files(self, repo_path):
        """Gets a list of all unstaged (modified or new) files, excluding deleted ones."""
        result = self._run_git_command(["git", "status", "--porcelain"], repo_path)
        if not result or not result.stdout.strip():
            return []
            
        changed_files = []
        for x in result.stdout.strip().split('\n'):
            line = x.lstrip().split(' ')
            status = line[0]
            filename = line[1] if len(line) > 1 else ""
            if status == '??' or 'M' in status:
                changed_files.append(filename)
        return changed_files
    
    def _prompt_for_files(self, files):
        """Shows an interactive checklist for the user to select files."""
        if not files:
            return []
        
        selected_files = questionary.checkbox(
            'Select files to include in the next AI analysis batch:',
            choices=sorted(files)
        ).ask()
        
        return selected_files

    def _get_staged_diff(self, repo_path, files_to_stage):
        """Stages the selected files and gets their combined diff."""
        print(f"{colors.OKCYAN}Staging selected files and getting diff...{colors.ENDC}")
        self._run_git_command(["git", "add", "--"] + files_to_stage, repo_path)
        result = self._run_git_command(["git", "diff", "--cached"], repo_path)
        return result.stdout if result else ""

    def _get_ai_suggestions(self, diff_content):
        """Sends the diff to the local Ollama API for suggestions."""
        print(f"{colors.OKCYAN}Asking local AI to analyze changes... (This may take a moment){colors.ENDC}")
        prompt = f"""
        [INST]
            You are an expert software developer with a keen eye for writing clean, concise, and informative Git commit messages. Your task is to analyze the following `git diff` output for multiple files and generate a conventional commit message. 
            **Analyze the following `git diff`:**
            ```diff
            {diff_content}
            ```
            **Based on your analysis, provide a commit message that adheres to the following best practices:**
            * **Follow the Conventional Commits specification.** The commit type must be one of the following: `feat`, `fix`, `docs`, `style`, `refactor`, `perf`, `test`, or `chore`.
            * **Write a concise summary of changes.** The summary should be no longer than 72 characters.
            * **Include a detailed description if necessary.** If the changes are complex, provide a more detailed explanation in the body of the commit message.
            * **If the changes are significant, suggest breaking them down into smaller, more atomic commits** and provide a suggested commit message for each logical change.
        [/INST]
        """

        payload = {"model": AI_MODEL, "prompt": prompt, "stream": False, "format": "json"}
        try:
            response = requests.post(OLLAMA_API_URL, json=payload, timeout=300)
            response.raise_for_status()
            response_data = response.json()
            json_text = response_data.get('response')
            return json.loads(json_text)
        except requests.exceptions.Timeout:
            print(f"{colors.FAIL}Error: The request to Ollama timed out.{colors.ENDC}")
            return None
        except Exception as e:
            print(f"{colors.FAIL}Error during AI request: {e}{colors.ENDC}")
            return None

    def _process_commits(self, suggestions, repo_path):
        """Iterates through suggestions and prompts the user to commit."""
        if not suggestions:
            print(f"{colors.WARNING}The AI did not suggest any commits for this batch.{colors.ENDC}")
            return

        print(f"\n{colors.HEADER}{colors.BOLD}AI has suggested {len(suggestions)} commit(s). Reviewing now...{colors.ENDC}")
        print("--------------------------------------------------")
        
        for i, commit_info in enumerate(suggestions):
            message = commit_info.get("summary_of_changes")
            files = commit_info.get("files")

            if not message or not files or not isinstance(files, list):
                print(f"{colors.WARNING}Skipping invalid AI suggestion: {commit_info}{colors.ENDC}")
                continue

            print(f"\n{colors.BOLD}Suggested Commit {i+1}/{len(suggestions)}:{colors.ENDC}")
            print(f"  {colors.OKGREEN}Message:{colors.ENDC} {message}")
            print(f"  {colors.OKBLUE}Files:{colors.ENDC}")
            for f in files: print(f"    - {f}")
            
            self._run_git_command(["git", "reset", "--", "."], repo_path)
            self._run_git_command(["git", "add", "--"] + files, repo_path)

            user_input = questionary.select(
                "Commit these files with this message?",
                choices=["Yes", "No", "Edit Message"]
            ).ask()

            if user_input == "Yes":
                self._run_git_command(["git", "commit", "-m", message], repo_path)
                print(f"{colors.OKGREEN}Commit successful!{colors.ENDC}")
            elif user_input == "Edit Message":
                edited_message = questionary.text("Enter new commit message:", default=message).ask()
                if edited_message:
                    self._run_git_command(["git", "commit", "-m", edited_message], repo_path)
                    print(f"{colors.OKGREEN}Commit successful with new message!{colors.ENDC}")
                else:
                    print(f"{colors.WARNING}Empty message. Skipping commit.{colors.ENDC}")
            else:
                print(f"{colors.WARNING}Skipping this commit.{colors.ENDC}")
        
        print(f"\n{colors.OKGREEN}Batch review complete.{colors.ENDC}")

    def commit(self, repo_path="."):
        """
        Main command to run the AI-powered commit process in a continuous loop.
        :param repo_path: Path to the root of the git repository.
        """
        abs_repo_path = os.path.abspath(repo_path)
        self._check_prerequisites(abs_repo_path)
        
        while True:
            self._run_git_command(["git", "reset", "--", "."], abs_repo_path)

            # PHASE 1: Handle deleted files automatically
            deleted_files = self._get_deleted_files(abs_repo_path)
            if deleted_files:
                self._commit_deleted_files(abs_repo_path, deleted_files)

            # Get the remaining modified and new files
            modified_and_new_files = self._get_modified_and_new_files(abs_repo_path)
            if not modified_and_new_files:
                print(f"\n{colors.OKGREEN}All changes have been committed. Great job!{colors.ENDC}")
                break

            # PHASE 2: Handle dependency updates automatically
            committed_deps = self._handle_dependency_updates(abs_repo_path, modified_and_new_files)
            
            # Filter out the dependency files that were just committed
            remaining_files = [f for f in modified_and_new_files if f not in committed_deps]
            
            # If nothing is left after auto-commits, we're done with this loop.
            if not remaining_files:
                print(f"\n{colors.OKGREEN}All changes have been committed. Great job!{colors.ENDC}")
                break

            # PHASE 3: Handle all other files interactively with AI
            print("\n" + "="*60)
            print(f"{colors.HEADER}{colors.BOLD}Starting new interactive commit batch...{colors.ENDC}")
            
            selected_files = self._prompt_for_files(remaining_files)
            if not selected_files:
                print(f"{colors.WARNING}No files selected. Exiting session.{colors.ENDC}")
                self._run_git_command(["git", "reset", "--", "."], abs_repo_path)
                break

            diff_content = self._get_staged_diff(abs_repo_path, selected_files)
            if not diff_content:
                print(f"{colors.WARNING}Selected files resulted in an empty diff. Unstaging and continuing...{colors.ENDC}")
                self._run_git_command(["git", "reset", "--", "."], abs_repo_path)
                continue
            
            ai_suggestions = self._get_ai_suggestions(diff_content)
            
            print(f"{colors.OKCYAN}Raw AI suggestions:{colors.ENDC}\n{ai_suggestions}")
            if not ai_suggestions or not ai_suggestions.get('commits'):
                print(f"{colors.WARNING}No valid suggestions received from AI. Unstaging files for new selection.{colors.ENDC}")
                self._run_git_command(["git", "reset", "--", "."], abs_repo_path)
                continue

            self._process_commits(ai_suggestions.get('commits'), abs_repo_path)
            self._run_git_command(["git", "reset", "--", "."], abs_repo_path)


if __name__ == "__main__":
    fire.Fire(GitAICommitter)
