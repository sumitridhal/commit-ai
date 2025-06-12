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
        """Gets a list of all changed files (modified, new). Excludes deleted files."""
        result = self._run_git_command(["git", "status", "--porcelain"], repo_path)
        if not result or not result.stdout.strip():
            return []
        
        files = []
        for line in result.stdout.strip().split('\n'):
            status, file_path = line.strip().split(maxsplit=1)
            # We are interested in Modified ('M') and Untracked ('??') files
            if status in ['M', '??']:
                 files.append({'status': status, 'file': file_path})
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
        
        prompt_intro = ""
        content_section = ""

        if status == '??': # Untracked file
            print(f"Analyzing new file: {colors.OKCYAN}{file_path}{colors.ENDC}")
            try:
                # For new files, analyze the entire content.
                full_path = os.path.join(repo_path, file_path)
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
        print(f"\n{colors.OKCYAN}🤖 Performing AI code review...{colors.ENDC}")
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

    def commit(self, repo_path=".", skip_reset=False, auto_mode=False):
        """Main command to run the AI-powered commit process."""
        abs_repo_path = os.path.abspath(repo_path)
        self._check_prerequisites(abs_repo_path)
        
        if auto_mode:
            print(f"\n{colors.HEADER}{colors.BOLD}🚀 Auto Mode Enabled{colors.ENDC}")
            # Safety check for auto mode
            proceed = questionary.confirm(
                "Auto mode will automatically group, generate messages, and commit changes. Are you sure you want to proceed?",
                default=False
            ).ask()
            if not proceed:
                print(f"{colors.WARNING}Auto mode cancelled by user.{colors.ENDC}")
                return

        while True:
            if not skip_reset:
                print(f"{colors.OKCYAN}Unstaging all files to ensure a clean slate...{colors.ENDC}")
                self._run_git_command(["git", "reset"], abs_repo_path)
            else:
                print(f"{colors.WARNING}Skipping initial 'git reset'. Analyzing working directory changes only.{colors.ENDC}")
            
            
            # PHASE 1: Handle deleted files
            self._auto_commit_deleted_files(abs_repo_path)
            
            # Get the list of remaining changed files (Modified and New)
            all_changed_files_info = self._get_changed_files(abs_repo_path)
            
            # Extract just the paths for dependency check
            all_changed_file_paths = [f['file'] for f in all_changed_files_info]
            if not all_changed_file_paths:
                print(f"\n{colors.OKGREEN}All changes have been committed. Great job! ✅{colors.ENDC}")
                break

            # PHASE 2: Handle dependency lock file updates
            self._auto_commit_dependency_updates(abs_repo_path, all_changed_file_paths)

            # Refresh the file list after auto-commits
            code_files_to_analyze = self._get_changed_files(abs_repo_path)

            if not code_files_to_analyze:
                print(f"\n{colors.OKGREEN}All changes have been committed. Great job! ✅{colors.ENDC}")
                break

            print("\n" + "="*60)
            print(f"{colors.HEADER}{colors.BOLD}Analyzing {len(code_files_to_analyze)} file(s) individually...{colors.ENDC}")
            
            # PHASE 3: Individual File Analysis
            file_analyses = []
            for file_info in code_files_to_analyze:
                analysis = self._analyze_single_file(abs_repo_path, file_info)
                
                if analysis and "summary" in analysis and "keywords" in analysis:
                    file_analyses.append({"file": file_info['file'], **analysis})

            if not file_analyses:
                print(f"{colors.WARNING}Could not analyze any files. Please check for errors.{colors.ENDC}")
                break
                
            # PHASE 4: Suggest Grouping
            keyword_groups = defaultdict(list)
            for analysis in file_analyses:
                if analysis["keywords"]:
                    # Use the first keyword as the primary grouping mechanism
                    keyword_groups[analysis["keywords"][0]].append(analysis["file"])
            
            remaining_files_to_commit = [analysis['file'] for analysis in file_analyses]
            
            while remaining_files_to_commit:
                choices = []
                # Create choices for grouped commits
                for keyword, files in keyword_groups.items():
                    group_files = [f for f in files if f in remaining_files_to_commit]
                    if len(group_files) > 1:
                        choices.append(questionary.Choice(
                            title=f"Group ({keyword}): Commit {len(group_files)} related files",
                            value={"type": "group", "files": group_files}
                        ))
                
                # Create choices for individual file commits
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

                # PHASE 5: Generate message and commit
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
                    choices=["Yes", "Edit", "No"]
                ).ask()

                if action == "Yes":
                    if self._commit_files(abs_repo_path, selected_files, commit_message, no_verify=True):
                        remaining_files_to_commit = [f for f in remaining_files_to_commit if f not in selected_files]
                elif action == "Edit":
                    edited_message = questionary.text("Edit the message:", default=commit_message).ask()
                    if edited_message and self._commit_files(abs_repo_path, selected_files, edited_message, no_verify=True):
                        remaining_files_to_commit = [f for f in remaining_files_to_commit if f not in selected_files]
                else:
                    print(f"{colors.WARNING}Commit skipped.{colors.ENDC}")


if __name__ == "__main__":
    fire.Fire(GitAICommitter)
