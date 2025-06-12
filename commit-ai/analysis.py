#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
AI-Powered Git Commit Script (Local Edition with Fire) - V2.1

This script uses a locally hosted Ollama model to analyze file changes,
including modified and new (untracked) files. It suggests logical commits
and generates conventional commit messages for them.

MODIFIED FLOW:
- Phase 1: Automatically detects and commits DELETED files first.
- Phase 2: Automatically detects and commits LOCK FILE updates (e.g., package-lock.json).
- Phase 3: Individually analyzes each remaining file (modified or new) using AI.
- Phase 4: Suggests grouping related files based on AI analysis.
- Phase 5: Generates a final commit message for the user-approved group.
- Phase 6 (Optional): After all commits, generates a PR/MR summary.

This tiered approach allows the AI to create more focused, atomic commits
by first understanding changes at a file level before proposing multi-file commits.

Author: Sumit Ridhal
Date: 2025-06-12 (Modified)

Prerequisites:
- Python 3.6+
- Git installed on your system.
- Ollama installed and running locally.

Setup:
1. Install Ollama for macOS from https://ollama.com/
2. Pull the model you want to use:
   ollama pull mistral-nemo
3. Install dependencies from the updated requirements.txt:
   pip install -r requirements.txt

How to Use:
1. Ensure the Ollama application is running.
2. For interactive mode, run the script as before:
    python ./analysis.py commit --repo-path='../../upgrade/connect'

3. For fully automatic mode, add the --auto-mode flag:
    python ./analysis.py commit --repo-path='../../upgrade/connect' --auto-mode

4. AI-Generated Test Skeletons
    python ./analysis.py test --file='src/components/NewFeature.tsx' --repo-path='../../upgrade/connect'

5. AI-Powered Branch Summary for Pull Requests
    python ./analysis.py summarize --base-branch='main' --repo-path='../../upgrade/connect'

6. Skip initial reset with auto mode:
    python ./analysis.py commit --repo-path='../../upgrade/connect' --auto-mode --skip-reset

7. Automatically generate a summary after all commits are done:
    python ./analysis.py commit --repo-path='path/to/repo' --auto-mode --summarize-after --base-branch='develop'
"""

import os
import sys
import subprocess
import json
import requests
import fire
import questionary
from collections import defaultdict

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

# Files to be committed automatically with a generic dependency message.
# package.json is intentionally excluded to allow for AI analysis of dependency changes.
GENERIC_DEPENDENCY_FILES = ['package-lock.json', 'yarn.lock', 'pnpm-lock.yaml']


class GitAICommitter:
    """A class to handle AI-powered Git commit operations."""

    def _run_git_command(self, command, repo_path):
        """Helper to run a git command in the specified repository path."""
        try:
            return subprocess.run(command, check=True, capture_output=True, text=True, cwd=repo_path)
        except subprocess.CalledProcessError as e:
            # Git status returns 1 if there are untracked files, which is not a failure.
            if e.returncode == 1 and "status" in command:
                 return e
            print(f"{colors.FAIL}Error running git command '{' '.join(command)}':\n{e.stderr}{colors.ENDC}")
            # Exit for critical errors, but not for non-zero exit codes that can be benign (like git status).
            if e.returncode != 1:
                sys.exit(1)
            return None

    def _check_prerequisites(self, repo_path):
        """Checks if Git is installed, Ollama is running, and the path is a git repo."""
        print(f"{colors.OKCYAN}Checking prerequisites...{colors.ENDC}")
        if not self._run_git_command(["git", "--version"], repo_path):
            sys.exit(1)

        if not os.path.isdir(os.path.join(repo_path, '.git')):
            print(f"{colors.FAIL}Error: The path '{repo_path}' is not a valid git repository.{colors.ENDC}")
            sys.exit(1)

        try:
            requests.get("http://localhost:11434", timeout=3)
        except requests.exceptions.ConnectionError:
            print(f"{colors.FAIL}Error: Could not connect to Ollama at http://localhost:11434.{colors.ENDC}")
            print(f"{colors.WARNING}Please ensure the Ollama application is running.{colors.ENDC}")
            sys.exit(1)
        
        print(f"{colors.OKGREEN}Prerequisites met.{colors.ENDC}")

    def _commit_files(self, repo_path, files, message, no_verify=True):
        """Stages a list of files and commits them with the given message."""
        try:
            # Use --add for untracked files and -u for modified files. Here we just add all.
            self._run_git_command(["git", "add", "--"] + files, repo_path)
            
            # Build the commit command
            commit_command = ["git", "commit", "-m", message]
            if no_verify:
                commit_command.append("--no-verify")
            
            # Run the commit
            self._run_git_command(commit_command, repo_path)

            print(f"{colors.OKGREEN}Successfully committed {len(files)} file(s).{colors.ENDC}")
            return True
        except Exception as e:
            print(f"{colors.FAIL}An error occurred during commit: {e}{colors.ENDC}")
            # Rollback staging if commit fails
            self._run_git_command(["git", "reset", "HEAD", "--"] + files, repo_path)
            return False

    def _get_changed_files(self, repo_path):
        """
        Gets a list of all changed files (modified, new, renamed, staged).
        Excludes deleted files, which are handled separately.

        This implementation uses a two-part approach:
        1. 'git status --porcelain' to find modified, staged, and renamed files.
        2. 'git ls-files --others --exclude-standard' to find new (untracked) files.
        """
        files = []
        # Use a set to prevent adding the same file twice.
        processed_files = set()

        # --- Part 1: Get modified, staged, and renamed files from 'git status' ---
        status_result = self._run_git_command(["git", "status", "--porcelain"], repo_path)
        if status_result and status_result.stdout.strip():
            for line in status_result.stdout.strip().split('\n'):
                status_code = line[:2]
                path_info = line[3:]

                # Unquote file paths that git status wraps in quotes
                if path_info.startswith('"') and path_info.endswith('"'):
                    path_info = path_info[1:-1]

                # Skip deleted files (handled separately) and untracked files (handled by ls-files).
                if 'D' in status_code or status_code == '??':
                    continue

                # Handle renamed files ('R  old -> new')
                if status_code.startswith('R'):
                    # The change is in the new path, which we analyze as a modification.
                    _, new_path = path_info.split(' -> ')
                    file_to_add = new_path
                    analysis_status = 'M'  # Treat rename as modification for diff analysis
                else:
                    # Handles all other changes like Modified ('M'), Added ('A'), etc.
                    file_to_add = path_info
                    # Staged new files ('A') or staged+modified ('AM') are treated as new files for analysis.
                    analysis_status = '??' if status_code.strip() in ['A', 'AM'] else 'M'

                if file_to_add and file_to_add not in processed_files:
                    files.append({'status': analysis_status, 'file': file_to_add})
                    processed_files.add(file_to_add)

        # --- Part 2: Get new/untracked files using 'git ls-files' ---
        # This is the most reliable command for listing only untracked files, respecting .gitignore.
        ls_files_result = self._run_git_command(
            ["git", "ls-files", "--others", "--exclude-standard"],
            repo_path
        )
        if ls_files_result and ls_files_result.stdout.strip():
            for untracked_file in ls_files_result.stdout.strip().split('\n'):
                if untracked_file and untracked_file not in processed_files:
                    files.append({'status': '??', 'file': untracked_file})
                    processed_files.add(untracked_file)

        return files

    def _auto_commit_deleted_files(self, repo_path):
        """Finds, stages, and commits deleted files automatically."""
        status_result = self._run_git_command(["git", "status", "--porcelain"], repo_path)
        if not status_result or not status_result.stdout: return
        
        # Deleted files are prefixed with ' D'
        deleted_files = [line.split(maxsplit=1)[1] for line in status_result.stdout.strip().split('\n') if line.startswith(' D')]
        if not deleted_files:
            return

        print(f"\n{colors.OKBLUE}Found {len(deleted_files)} deleted file(s). Committing them...{colors.ENDC}")
        for f in deleted_files:
            print(f"  - {colors.WARNING}{f}{colors.ENDC}")
        
        # For deleted files, `git add -u` or `git rm` is needed. `git add --` also works.
        self._commit_files(repo_path, deleted_files, "refactor(cleanup): remove deleted files")

    def _auto_commit_dependency_updates(self, repo_path, all_changed_files):
        """Finds, stages, and commits dependency lock file updates automatically."""
        deps_to_commit = [f for f in all_changed_files if os.path.basename(f) in GENERIC_DEPENDENCY_FILES]
        
        if not deps_to_commit:
            return

        print(f"\n{colors.OKBLUE}Found lock file updates. Committing them automatically...{colors.ENDC}")
        for f in deps_to_commit:
            print(f"  - {colors.OKCYAN}{f}{colors.ENDC}")

        self._commit_files(repo_path, deps_to_commit, "chore(deps): update dependencies", no_verify=True)

    def _analyze_single_file(self, repo_path, file_info):
        """Gets AI analysis for a single file, handling both new and modified files."""
        file_path = file_info['file']
        status = file_info['status']

        full_path = os.path.join(repo_path, file_path)
        if os.path.isdir(full_path):
            print(f"{colors.WARNING}Skipping directory: {file_path}{colors.ENDC}")
            return None

        prompt_intro = ""
        content_section = ""

        if status == '??': # Untracked file
            print(f"Analyzing new file: {colors.OKCYAN}{file_path}{colors.ENDC}")
            try:
                # For new files, analyze the entire content.
                with open(full_path, 'r', encoding='utf-8', errors='ignore') as f:
                    content = f.read()
                if not content.strip():
                    print(f"{colors.WARNING}Skipping empty new file: {file_path}{colors.ENDC}")
                    return None
                prompt_intro = f"You are an expert developer. The following is a **new file** named `{file_path}`. Analyze its content to understand its purpose."
                content_section = f"**File Content:**\n```\n{content}\n```"
            except Exception as e:
                print(f"{colors.FAIL}Error reading new file {file_path}: {e}{colors.ENDC}")
                return None
        else: # Modified file
            print(f"Analyzing modified file: {colors.OKCYAN}{file_path}{colors.ENDC}")
            diff_content_result = self._run_git_command(["git", "diff", "--", file_path], repo_path)
            if not diff_content_result or not diff_content_result.stdout:
                return None # No changes to analyze
            content = diff_content_result.stdout
            prompt_intro = f"You are an expert developer. Analyze the following git diff for the file `{file_path}`."
            content_section = f"**Git Diff:**\n```diff\n{content}\n```"

        prompt = f"""
        [INST]
        {prompt_intro}
        Your goal is to understand the change and categorize it.

        {content_section}

        **Your Task:**
        Provide a JSON object with two keys:
        1. "summary": A very brief, one-sentence summary of the file's purpose or its changes.
        2. "keywords": An array of 1-3 lowercase string keywords that categorize the change (e.g., ["auth", "ui", "api-client"]).

        Example Response:
        {{
            "summary": "Adds a new loading spinner component for data fetching.",
            "keywords": ["ui", "component", "loading-state"]
        }}
        [/INST]
        """
        payload = {"model": AI_MODEL, "prompt": prompt, "stream": False, "format": "json"}
        try:
            response = requests.post(OLLAMA_API_URL, json=payload, timeout=120)
            response.raise_for_status()
            response_data = response.json()
            return json.loads(response_data.get('response', '{}'))
        except (requests.exceptions.RequestException, json.JSONDecodeError) as e:
            print(f"{colors.FAIL}Error analyzing {file_path}: {e}{colors.ENDC}")
            return None
            
    def _get_ai_review(self, repo_path, files):
        """Generates an AI-powered code review for a set of files."""
        print(f"\n{colors.OKCYAN}Performing AI code review...{colors.ENDC}")
        # Use the same logic as _generate_commit_message_for_group to get the diff
        self._run_git_command(["git", "add", "--"] + files, repo_path)
        diff_result = self._run_git_command(["git", "diff", "--cached"], repo_path)
        self._run_git_command(["git", "reset", "HEAD", "--"] + files, repo_path)

        if not diff_result or not diff_result.stdout:
            print(f"{colors.WARNING}Could not generate diff for review.{colors.ENDC}")
            return None

        prompt = f"""
        [INST]
        You are an expert code reviewer with a keen eye for detail.
        Analyze the following git diff and provide constructive feedback.

        **Combined Git Diff:**
        ```diff
        {diff_result.stdout}
        ```

        **Your Task:**
        Review the code changes for potential issues. Focus on:
        - Logic errors or potential bugs.
        - Performance optimizations.
        - Readability and code style inconsistencies.
        - Lack of necessary comments or documentation.
        - Security vulnerabilities (e.g., hardcoded secrets, injection risks).

        Provide your feedback as a JSON object with a single key "review_comments", which is a list of strings. If there are no issues, return an empty list.

        Example Response:
        {{
            "review_comments": [
                "In `auth.js`, the session token appears to have a very long expiration. Consider shortening it.",
                "The new `calculateTotal` function could be simplified using `Array.reduce()`.",
                "Missing docstring for the public function `createNewUser`."
            ]
        }}
        [/INST]
        """
        payload = {"model": AI_MODEL, "prompt": prompt, "stream": False, "format": "json"}
        try:
            response = requests.post(OLLAMA_API_URL, json=payload, timeout=180)
            response.raise_for_status()
            response_data = json.loads(response.json().get('response', '{}'))
            return response_data.get("review_comments")
        except (requests.exceptions.RequestException, json.JSONDecodeError) as e:
            print(f"{colors.FAIL}Error generating AI review: {e}{colors.ENDC}")
            return None

    def _generate_commit_message_for_group(self, repo_path, files):
        """Generates a final commit message for a group of files."""
        print(f"\n{colors.OKBLUE}Generating final commit message for {len(files)} file(s)...{colors.ENDC}")
        # Stage files temporarily to get a combined diff for all changes
        self._run_git_command(["git", "add", "--"] + files, repo_path)
        diff_result = self._run_git_command(["git", "diff", "--cached"], repo_path)
        # Unstage them immediately after getting the diff
        self._run_git_command(["git", "reset", "HEAD", "--"] + files, repo_path)

        if not diff_result or not diff_result.stdout:
            print(f"{colors.WARNING}Could not generate diff for the selected group.{colors.ENDC}")
            return None
        
        prompt = f"""
        [INST]
        You are an expert at writing conventional git commit messages.
        Analyze the combined git diff for the following files: {', '.join(files)}.

        **Combined Git Diff:**
        ```diff
        {diff_result.stdout}
        ```

        **Your Task:**
        Generate a conventional commit message. The message should have a `type`, an optional `scope`, and a concise `summary`. Add a `body` for more detailed explanations if necessary.
        
        Return a single JSON object with the key "commit_message".

        Example Response:
        {{
            "commit_message": "feat(auth): implement user logout flow\\n\\nAdds a new API endpoint for logging out and connects it to the user profile page. This invalidates the user's session token on the server."
        }}
        [/INST]
        """
        payload = {"model": AI_MODEL, "prompt": prompt, "stream": False, "format": "json"}
        try:
            response = requests.post(OLLAMA_API_URL, json=payload, timeout=180)
            response.raise_for_status()
            response_data = json.loads(response.json().get('response', '{}'))
            return response_data.get("commit_message")
        except (requests.exceptions.RequestException, json.JSONDecodeError) as e:
            print(f"{colors.FAIL}Error generating commit message: {e}{colors.ENDC}")
            return None

    def test(self, file_path, repo_path="."):
        """Generates a test skeleton for changes in a specific file."""
        abs_repo_path = os.path.abspath(repo_path)
        self._check_prerequisites(abs_repo_path)

        print(f"Generating test skeleton for: {colors.OKCYAN}{file_path}{colors.ENDC}")
        diff_content_result = self._run_git_command(["git", "diff", "HEAD", "--", file_path], abs_repo_path)
        if not diff_content_result or not diff_content_result.stdout:
            print(f"{colors.WARNING}No changes found for {file_path} compared to HEAD. Showing diff from working tree.{colors.ENDC}")
            diff_content_result = self._run_git_command(["git", "diff", "--", file_path], abs_repo_path)

        if not diff_content_result or not diff_content_result.stdout:
            print(f"{colors.FAIL}No changes to analyze in {file_path}.{colors.ENDC}")
            return

        prompt = f"""
        [INST]
        You are an expert in software testing. Based on the following git diff for the file `{file_path}`, generate a skeleton for a unit test file.

        **Git Diff:**
        ```diff
        {diff_content_result.stdout}
        ```

        **Your Task:**
        - Identify the new or modified functions/classes/components.
        - Create a basic test structure (e.g., using Jest, PyTest, JUnit, etc., based on the file extension).
        - Include `describe`/`suite` blocks for organization.
        - Add empty `it`/`test` blocks with descriptive names for the main logic paths (e.g., success case, error case, edge cases).
        - Use `// TODO:` or `# TODO:` comments inside the test blocks to indicate where the developer should fill in the assertions.

        Return the test code as a single plain text block.
        [/INST]
        """
        # NOTE: For this prompt, we don't use "format: json" because we want a raw code block.
        payload = {"model": AI_MODEL, "prompt": prompt, "stream": False}
        try:
            response = requests.post(OLLAMA_API_URL, json=payload, timeout=180)
            response.raise_for_status()
            generated_test = response.json().get('response', '')

            print("\n" + "="*20 + " AI-Generated Test Skeleton " + "="*20)
            print(f"{colors.OKGREEN}{generated_test}{colors.ENDC}")
            print("="*66)
            print(f"{colors.WARNING}Note: This is a starting point. Please review and complete the test logic.{colors.ENDC}")

        except requests.exceptions.RequestException as e:
            print(f"{colors.FAIL}Error generating test skeleton: {e}{colors.ENDC}")

    def summarize(self, base_branch="origin/main", head_branch=None, repo_path="."):
        """Generates a summary of all changes on the current branch for a PR."""
        abs_repo_path = os.path.abspath(repo_path)
        self._check_prerequisites(abs_repo_path)

        # If head_branch is not provided, automatically determine the current branch
        if head_branch is None:
            try:
                head_branch_result = self._run_git_command(["git", "rev-parse", "--abbrev-ref", "HEAD"], abs_repo_path)
                if not head_branch_result or not head_branch_result.stdout:
                    print(f"{colors.FAIL}Could not automatically determine the current branch.{colors.ENDC}")
                    return
                head_branch = head_branch_result.stdout.strip()
            except Exception as e:
                print(f"{colors.FAIL}Error determining current branch: {e}{colors.ENDC}")
                return

        print(f"Summarizing changes on '{colors.OKCYAN}{head_branch}{colors.ENDC}' against base '{colors.OKCYAN}{base_branch}{colors.ENDC}'...")

        # Get the combined diff of the entire branch
        diff_command = ["git", "diff", f"{base_branch}..{head_branch}"]
        diff_result = self._run_git_command(diff_command, abs_repo_path)

        if not diff_result or not diff_result.stdout:
            print(f"{colors.WARNING}No differences found between '{head_branch}' and '{base_branch}'.{colors.ENDC}")
            return

        prompt = f"""
        [INST]
        You are an expert technical writer. Based on the following combined git diff from a feature branch, generate a summary for a Pull Request description.

        **Combined Branch Diff:**
        ```diff
        {diff_result.stdout}
        ```

        **Your Task:**
        Write a clear and concise PR description. It should include:
        1.  **A high-level summary:** What is the main purpose of this PR?
        2.  **A bulleted list of key changes:** Detail the most important additions, fixes, or refactors.
        3.  **Potential impact:** Mention any breaking changes or areas reviewers should pay close attention to.

        Format the output as clean Markdown.
        [/INST]
        """
        payload = {"model": AI_MODEL, "prompt": prompt, "stream": False}
        try:
            response = requests.post(OLLAMA_API_URL, json=payload, timeout=240)
            response.raise_for_status()
            summary = response.json().get('response', '')

            print("\n" + "="*20 + " AI-Generated Pull Request Summary " + "="*20)
            print(f"{colors.OKCYAN}{summary}{colors.ENDC}")
            print("="*70)

        except requests.exceptions.RequestException as e:
            print(f"{colors.FAIL}Error generating branch summary: {e}{colors.ENDC}")

    def commit(self, repo_path=".", skip_reset=False, auto_mode=False, summarize_after=False, base_branch="origin/main"):
        """Main command to run the AI-powered commit process."""
        abs_repo_path = os.path.abspath(repo_path)
        self._check_prerequisites(abs_repo_path)
        
        if auto_mode:
            print(f"\n{colors.HEADER}{colors.BOLD}Auto Mode Enabled{colors.ENDC}")
            # Safety check for auto mode
            proceed = questionary.confirm(
                "Auto mode will automatically group, generate messages, and commit changes. Are you sure you want to proceed?",
                default=False
            ).ask()
            if not proceed:
                print(f"{colors.WARNING}Auto mode cancelled by user.{colors.ENDC}")
                return

        while True:
            # ... (the initial git reset and auto-commit for deleted/lock files remains the same) ...
            if not skip_reset:
                print(f"{colors.OKCYAN}Unstaging all files to ensure a clean slate...{colors.ENDC}")
                self._run_git_command(["git", "reset"], abs_repo_path)
            else:
                print(f"{colors.WARNING}Skipping initial 'git reset'. Analyzing working directory changes only.{colors.ENDC}")
            
            self._auto_commit_deleted_files(abs_repo_path)
            all_changed_files_info = self._get_changed_files(abs_repo_path)
            all_changed_file_paths = [f['file'] for f in all_changed_files_info]
            if not all_changed_file_paths:
                print(f"\n{colors.OKGREEN}All changes have been committed. Great job! {colors.ENDC}")
                break
            self._auto_commit_dependency_updates(abs_repo_path, all_changed_file_paths)
            code_files_to_analyze = self._get_changed_files(abs_repo_path)
            if not code_files_to_analyze:
                print(f"\n{colors.OKGREEN}All changes have been committed. Great job! {colors.ENDC}")
                break

            print("\n" + "="*60)
            print(f"{colors.HEADER}{colors.BOLD}Analyzing {len(code_files_to_analyze)} file(s) individually...{colors.ENDC}")
            
            file_analyses = []
            for file_info in code_files_to_analyze:
                analysis = self._analyze_single_file(abs_repo_path, file_info)
                if analysis and "summary" in analysis and "keywords" in analysis:
                    file_analyses.append({"file": file_info['file'], **analysis})

            if not file_analyses:
                print(f"{colors.WARNING}Could not analyze any files. Please check for errors.{colors.ENDC}")
                break
                
            keyword_groups = defaultdict(list)
            for analysis in file_analyses:
                if analysis["keywords"]:
                    keyword_groups[analysis["keywords"][0]].append(analysis["file"])
            
            remaining_files_to_commit = [analysis['file'] for analysis in file_analyses]
            
            if auto_mode:
                print(f"{colors.OKBLUE}\nRunning automatic commit selection...{colors.ENDC}")
                
                prioritized_groups = sorted(
                    [files for files in keyword_groups.values() if len(files) > 1],
                    key=len,
                    reverse=True
                )

                selected_files = []
                if prioritized_groups:
                    selected_files = prioritized_groups[0]
                    print(f"Found group of {len(selected_files)} files to commit.")
                elif remaining_files_to_commit:
                    selected_files = [remaining_files_to_commit[0]]
                    print("No more groups found. Committing single file.")
                else:
                    break

                commit_message = self._generate_commit_message_for_group(abs_repo_path, selected_files)

                if commit_message:
                    print("\n" + "-"*20)
                    print(f"{colors.OKGREEN}Generated Commit Message:{colors.ENDC}")
                    print(f"{colors.BOLD}{commit_message}{colors.ENDC}")
                    print(f"{colors.OKBLUE}Files included:{colors.ENDC}")
                    for f in selected_files: print(f"  - {f}")
                    print("-" * 20)
                    print(f"{colors.OKCYAN}Applying commit automatically...{colors.ENDC}")
                    self._commit_files(abs_repo_path, selected_files, commit_message, no_verify=True)
                else:
                    print(f"{colors.FAIL}AI failed to generate a message for {selected_files}. Skipping them.{colors.ENDC}")
                    remaining_files_to_commit = [f for f in remaining_files_to_commit if f not in selected_files]
                    if not remaining_files_to_commit:
                         break
                
                continue 
            else:
                while remaining_files_to_commit:
                    choices = []
                    current_keyword_groups = defaultdict(list)
                    for f in remaining_files_to_commit:
                        analysis = next((a for a in file_analyses if a['file'] == f), None)
                        if analysis and analysis['keywords']:
                            current_keyword_groups[analysis['keywords'][0]].append(f)

                    for keyword, files in current_keyword_groups.items():
                        if len(files) > 1:
                            choices.append(questionary.Choice(
                                title=f"Group ({keyword}): Commit {len(files)} related files",
                                value={"type": "group", "files": files}
                            ))
                    
                    choices.append(questionary.Separator())
                    for f in sorted(remaining_files_to_commit):
                        choices.append(questionary.Choice(title=f"Single: {f}", value={"type": "single", "files": [f]}))
                    
                    choices.append(questionary.Separator())
                    choices.append(questionary.Choice(title="Exit", value={"type": "exit"}))

                    selection = questionary.select(
                        "Select a commit action:",
                        choices=choices
                    ).ask()

                    if not selection or selection['type'] == 'exit':
                        print(f"{colors.WARNING}Exiting commit session.{colors.ENDC}")
                        self._run_git_command(["git", "reset"], abs_repo_path)
                        return

                    selected_files = selection['files']
                    commit_message = self._generate_commit_message_for_group(abs_repo_path, selected_files)

                    if not commit_message:
                        print(f"{colors.WARNING}AI failed to generate a message. Please try again.{colors.ENDC}")
                        continue

                    print("\n" + "-"*20)
                    print(f"{colors.OKGREEN}Suggested Commit Message:{colors.ENDC}")
                    print(f"{colors.BOLD}{commit_message}{colors.ENDC}")
                    print(f"{colors.OKBLUE}Files included:{colors.ENDC}")
                    for f in selected_files: print(f"  - {f}")
                    print("-" * 20)
                    
                    action = questionary.select(
                        "Apply this commit?",
                        choices=["Yes", "Edit","Get AI Review", "No"]
                    ).ask()

                    if action == "Yes":
                        if self._commit_files(abs_repo_path, selected_files, commit_message, no_verify=True):
                            remaining_files_to_commit = [f for f in remaining_files_to_commit if f not in selected_files]
                    elif action == "Edit":
                        edited_message = questionary.text("Edit the message:", default=commit_message).ask()
                        if edited_message and self._commit_files(abs_repo_path, selected_files, edited_message, no_verify=True):
                            remaining_files_to_commit = [f for f in remaining_files_to_commit if f not in selected_files]
                    elif action == "Get AI Review":
                        review_comments = self._get_ai_review(abs_repo_path, selected_files)
                        if review_comments:
                            print(f"\n{colors.HEADER}{colors.BOLD}AI Review Feedback:{colors.ENDC}")
                            for comment in review_comments:
                                print(f"  - {colors.WARNING}{comment}{colors.ENDC}")
                            print("\nConsider addressing the feedback before committing.")
                        elif review_comments == []:
                            print(f"\n{colors.OKGREEN}AI Review complete. No issues found! {colors.ENDC}")
                    else:
                        print(f"{colors.WARNING}Commit skipped.{colors.ENDC}")
                
                break
        
        # <<< --- NEW FEATURE: Summarize after committing --- >>>
        if summarize_after:
            print(f"\n{colors.HEADER}{colors.BOLD}All commits complete. Generating Pull Request summary...{colors.ENDC}")
            try:
                # The 'head_branch' will be auto-detected by the summarize function
                self.summarize(base_branch=base_branch, repo_path=abs_repo_path)
            except Exception as e:
                print(f"{colors.FAIL}An error occurred while generating the summary: {e}{colors.ENDC}")


if __name__ == "__main__":
    fire.Fire(GitAICommitter)