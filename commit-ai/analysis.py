#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
AI-Powered Git Commit Script with Google Gemini - V3.0

This script uses Google Gemini to analyze file changes, including modified and new files.
It intelligently groups files by dependencies and related features to create logical,
atomic commits with conventional commit messages.

ENHANCED FEATURES (v3.0):
- Google Gemini integration for superior analysis
- Intelligent file batching based on dependencies and feature relationships
- Enhanced commit message generation with context awareness
- Better error handling and retry logic
- Improved code review capabilities
- Feature-based file grouping for logical commits

Author: Sumit Ridhal
Date: 2025-01-20 (Enhanced with Gemini)

Prerequisites:
- Python 3.8+
- Git installed on your system
- Google Gemini API key

Setup:
1. Install dependencies: pip install -r requirements.txt
2. Set your Gemini API key in the script or environment variable

Usage:
    python analysis.py commit --repo-path='/path/to/your/repo'
    python analysis.py commit --repo-path='/path/to/your/repo' --auto-mode
    python analysis.py test --file='src/component.tsx' --repo-path='/path/to/your/repo'
    python analysis.py summarize --base-branch='main' --repo-path='/path/to/your/repo'
"""

import os
import sys
import subprocess
import json
import fire
import questionary
import google.generativeai as genai
from collections import defaultdict
from typing import Dict, List, Optional, Set
from retry import retry
from dotenv import load_dotenv
import re
import logging
from datetime import datetime
from pathlib import Path

# Load environment variables
load_dotenv()

# --- Configuration ---
GEMINI_API_KEY = os.getenv('GEMINI_API_KEY')
GEMINI_MODEL = os.getenv('GEMINI_MODEL')

# Configure Gemini
genai.configure(api_key=GEMINI_API_KEY)

# ANSI color codes for better terminal output
class Colors:
    HEADER = '\033[95m'
    OKBLUE = '\033[94m'
    OKCYAN = '\033[96m'
    OKGREEN = '\033[92m'
    WARNING = '\033[93m'
    FAIL = '\033[91m'
    ENDC = '\033[0m'
    BOLD = '\033[1m'
    UNDERLINE = '\033[4m'

# Files to be committed automatically with generic messages
GENERIC_DEPENDENCY_FILES = ['package-lock.json', 'yarn.lock', 'pnpm-lock.yaml', 'poetry.lock', 'Pipfile.lock']

# Image file extensions to auto-commit
IMAGE_FILE_EXTENSIONS = ['.png', '.jpg', '.jpeg', '.svg', '.gif', '.webp', '.ico', '.bmp']

# File patterns that suggest dependencies or related features
DEPENDENCY_PATTERNS = {
    'package_management': ['.json', '.lock', '.toml', '.yaml', '.yml'],
    'configuration': ['config', 'env', 'settings', '.rc', '.conf'],
    'documentation': ['.md', '.rst', '.txt', 'README', 'CHANGELOG'],
    'testing': ['test', 'spec', '__tests__', '.test.', '.spec.'],
    'styling': ['.css', '.scss', '.sass', '.less', '.styl'],
    'types': ['.d.ts', 'types', 'interfaces'],
    'build': ['webpack', 'rollup', 'vite', 'tsconfig', 'babel', 'eslint', 'prettier'],
}

class EnhancedGitAICommitter:
    """Enhanced Git AI Committer with Gemini integration and intelligent file batching."""
    
    def __init__(self):
        self.gemini_model = genai.GenerativeModel(GEMINI_MODEL)
        self.colors = Colors()
        self.logger = None
        
    def _setup_logging(self, repo_path: str) -> None:
        """Sets up logging for the analysis session."""
        # Create logs directory in the current project (where analysis.py is located)
        script_dir = os.path.dirname(os.path.abspath(__file__))
        logs_dir = os.path.join(script_dir, "logs")
        os.makedirs(logs_dir, exist_ok=True)
        
        # Create log filename with timestamp
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        log_filename = f"commit_analysis_{timestamp}.log"
        log_path = os.path.join(logs_dir, log_filename)
        
        # Setup logger
        self.logger = logging.getLogger('git_ai_committer')
        self.logger.setLevel(logging.DEBUG)
        
        # Remove existing handlers
        for handler in self.logger.handlers[:]:
            self.logger.removeHandler(handler)
        
        # Create file handler
        file_handler = logging.FileHandler(log_path, encoding='utf-8')
        file_handler.setLevel(logging.DEBUG)
        
        # Create formatter
        formatter = logging.Formatter(
            '%(asctime)s - %(levelname)s - %(message)s',
            datefmt='%Y-%m-%d %H:%M:%S'
        )
        file_handler.setFormatter(formatter)
        
        # Add handler to logger
        self.logger.addHandler(file_handler)
        
        # Log session start
        self.logger.info("=== Git AI Committer Session Started ===")
        self.logger.info(f"Analyzing repository: {repo_path}")
        self.logger.info(f"Log file: {log_path}")
        
        print(f"📝 Logging to: {self.colors.OKCYAN}{log_path}{self.colors.ENDC}")
        
        # Store log path for reference
        self.log_path = log_path

    def _log_and_print(self, message: str, level: str = 'info') -> None:
        """Logs message to file and prints to console."""
        # Print to console
        print(message)
        
        # Log to file if logger is set up
        if self.logger:
            # Remove ANSI color codes for log file
            clean_message = re.sub(r'\033\[[0-9;]*m', '', message)
            
            if level.lower() == 'debug':
                self.logger.debug(clean_message)
            elif level.lower() == 'warning':
                self.logger.warning(clean_message)
            elif level.lower() == 'error':
                self.logger.error(clean_message)
            else:
                self.logger.info(clean_message)

    def _run_git_command(self, command: List[str], repo_path: str) -> Optional[subprocess.CompletedProcess]:
        """Helper to run a git command in the specified repository path."""
        try:
            return subprocess.run(command, check=True, capture_output=True, text=True, cwd=repo_path)
        except subprocess.CalledProcessError as e:
            if e.returncode == 1 and "status" in command:
                return e
            print(f"{self.colors.FAIL}Error running git command '{' '.join(command)}':\n{e.stderr}{self.colors.ENDC}")
            if e.returncode != 1:
                sys.exit(1)
            return None

    def _check_prerequisites(self, repo_path: str) -> None:
        """Checks if Git is installed and the path is a git repo."""
        print(f"{self.colors.OKCYAN}Checking prerequisites...{self.colors.ENDC}")
        
        if not self._run_git_command(["git", "--version"], repo_path):
            sys.exit(1)

        if not os.path.isdir(os.path.join(repo_path, '.git')):
            print(f"{self.colors.FAIL}Error: '{repo_path}' is not a valid git repository.{self.colors.ENDC}")
            sys.exit(1)

        if not GEMINI_API_KEY:
            print(f"{self.colors.FAIL}Error: GEMINI_API_KEY not configured.{self.colors.ENDC}")
            sys.exit(1)
        
        print(f"{self.colors.OKGREEN}Prerequisites met.{self.colors.ENDC}")

    def _commit_files(self, repo_path: str, files: List[str], message: str, no_verify: bool = True) -> bool:
        """Stages and commits files with the given message."""
        try:
            self._run_git_command(["git", "add", "--"] + files, repo_path)
            
            commit_command = ["git", "commit", "-m", message]
            if no_verify:
                commit_command.append("--no-verify")
            
            self._run_git_command(commit_command, repo_path)
            print(f"{self.colors.OKGREEN}Successfully committed {len(files)} file(s).{self.colors.ENDC}")
            return True
        except Exception as e:
            print(f"{self.colors.FAIL}Error during commit: {e}{self.colors.ENDC}")
            self._run_git_command(["git", "reset", "HEAD", "--"] + files, repo_path)
            return False

    def _get_changed_files(self, repo_path: str) -> List[Dict[str, str]]:
        """Gets all changed files with their status."""
        files = []
        processed_files = set()

        # First, get untracked files explicitly
        ls_files_result = self._run_git_command(["git", "ls-files", "--others", "--exclude-standard"], repo_path)
        if ls_files_result and ls_files_result.stdout.strip():
            for untracked_file in ls_files_result.stdout.strip().split('\n'):
                if untracked_file and untracked_file not in processed_files:
                    files.append({'status': '??', 'file': untracked_file})
                    processed_files.add(untracked_file)
                    if self.logger:
                        self.logger.debug(f"Found untracked file: {untracked_file}")

        # Then get tracked files with changes
        status_result = self._run_git_command(["git", "status", "--porcelain"], repo_path)
        if status_result and status_result.stdout.strip():
            for line in status_result.stdout.strip().split('\n'):
                if not line.strip():
                    continue
                    
                parts = line.strip().split(' ', 1)
                if len(parts) < 2:
                    continue
                    
                status_code = parts[0]
                path_info = parts[1]

                # Clean up quoted paths
                if path_info.startswith('"') and path_info.endswith('"'):
                    path_info = path_info[1:-1]

                # Skip deleted files
                if 'D' in status_code:
                    continue

                # Handle different status codes
                if status_code.startswith('R'):
                    # Renamed file: extract new path
                    if ' -> ' in path_info:
                        _, new_path = path_info.split(' -> ', 1)
                        file_to_add = new_path
                        analysis_status = 'M'  # Treat renamed as modified
                    else:
                        file_to_add = path_info
                        analysis_status = 'M'
                elif status_code == '??':
                    # Untracked file (might be duplicate from ls-files)
                    file_to_add = path_info
                    analysis_status = '??'
                elif status_code.strip() in ['A', 'AM']:
                    # Added files (new to git but staged)
                    file_to_add = path_info
                    analysis_status = '??'  # Treat as new file for analysis
                else:
                    # Modified files
                    file_to_add = path_info
                    analysis_status = 'M'

                if file_to_add and file_to_add not in processed_files:
                    files.append({'status': analysis_status, 'file': file_to_add})
                    processed_files.add(file_to_add)
                    if self.logger:
                        self.logger.debug(f"Found {analysis_status} file: {file_to_add} (git status: {status_code})")

        if self.logger:
            self.logger.info(f"Total files found: {len(files)}")
            for file_info in files:
                self.logger.info(f"  {file_info['status']}: {file_info['file']}")

        return files

    def _auto_commit_deleted_files(self, repo_path: str) -> None:
        """Automatically commits deleted files."""
        status_result = self._run_git_command(["git", "status", "--porcelain"], repo_path)
        if not status_result or not status_result.stdout:
            return
        
        deleted_files = []
        for line in status_result.stdout.strip().split('\n'):
            if line.strip().startswith('D'):
                deleted_files.append(line.split(maxsplit=1)[1])
        
        if deleted_files:
            print(f"{self.colors.WARNING}Auto-committing {len(deleted_files)} deleted file(s)...{self.colors.ENDC}")
            self._commit_files(repo_path, deleted_files, "chore: remove deleted files", no_verify=True)

    def _auto_commit_dependency_updates(self, repo_path: str, all_files: List[str]) -> None:
        """Automatically commits dependency files."""
        deps_to_commit = [f for f in all_files if any(dep in f for dep in GENERIC_DEPENDENCY_FILES)]
        if deps_to_commit:
            print(f"{self.colors.WARNING}Auto-committing {len(deps_to_commit)} dependency file(s)...{self.colors.ENDC}")
            self._commit_files(repo_path, deps_to_commit, "chore(deps): update dependencies", no_verify=True)

    def _auto_commit_image_files(self, repo_path: str, all_files: List[str]) -> None:
        """Automatically commits image files."""
        image_files = [f for f in all_files if any(f.lower().endswith(ext) for ext in IMAGE_FILE_EXTENSIONS)]
        if image_files:
            print(f"{self.colors.WARNING}Auto-committing {len(image_files)} image file(s)...{self.colors.ENDC}")
            
            # Group images by type for better commit messages
            new_images = []
            updated_images = []
            
            for image_file in image_files:
                # Check if this is a new file (untracked)
                status_result = self._run_git_command(["git", "status", "--porcelain", image_file], repo_path)
                if status_result and status_result.stdout.strip().startswith('??'):
                    new_images.append(image_file)
                else:
                    updated_images.append(image_file)
            
            # Commit new images
            if new_images:
                message = f"feat(assets): add {len(new_images)} new image{'s' if len(new_images) > 1 else ''}"
                self._commit_files(repo_path, new_images, message, no_verify=True)
            
            # Commit updated images
            if updated_images:
                message = f"chore(assets): update {len(updated_images)} image{'s' if len(updated_images) > 1 else ''}"
                self._commit_files(repo_path, updated_images, message, no_verify=True)

    def _classify_file_by_pattern(self, file_path: str) -> Set[str]:
        """Classifies a file based on its path and name patterns."""
        classifications = set()
        file_lower = file_path.lower()
        
        for category, patterns in DEPENDENCY_PATTERNS.items():
            for pattern in patterns:
                if pattern.lower() in file_lower:
                    classifications.add(category)
                    break
        
        # Additional logic for specific file types
        if file_path.endswith(('.ts', '.tsx', '.js', '.jsx')):
            classifications.add('source_code')
        elif file_path.endswith(('.py', '.java', '.cpp', '.c', '.go', '.rs')):
            classifications.add('source_code')
        elif file_path.endswith(('.html', '.vue', '.svelte')):
            classifications.add('template')
        elif file_path.endswith(('.json', '.yaml', '.yml', '.toml')):
            classifications.add('configuration')
        elif any(file_path.lower().endswith(ext) for ext in IMAGE_FILE_EXTENSIONS):
            classifications.add('assets')
            
        return classifications

    @retry(tries=3, delay=1, backoff=2)
    def _analyze_single_file(self, repo_path: str, file_info: Dict[str, str]) -> Optional[Dict]:
        """Analyzes a single file using Gemini."""
        file_path = file_info['file']
        status = file_info['status']
        full_path = os.path.join(repo_path, file_path)
        
        self._log_and_print(f"🔍 Analyzing {'new' if status == '??' else 'modified'} file: {self.colors.OKCYAN}{file_path}{self.colors.ENDC}")
        self._log_and_print(f"   📁 Full path: {full_path}", 'debug')
        self._log_and_print(f"   📊 Status: {status}", 'debug')
        
        if os.path.isdir(full_path):
            self._log_and_print(f"   ⚠️  {self.colors.WARNING}Skipping directory: {file_path}{self.colors.ENDC}", 'warning')
            return None

        # Check if file exists
        if not os.path.exists(full_path):
            self._log_and_print(f"   ❌ {self.colors.FAIL}File does not exist: {file_path}{self.colors.ENDC}", 'error')
            return None

        try:
            if status == '??':  # New file
                self._log_and_print(f"   📄 Processing new file...", 'debug')
                try:
                    with open(full_path, 'r', encoding='utf-8', errors='ignore') as f:
                        content = f.read()
                    self._log_and_print(f"   📝 File content length: {len(content)} characters", 'debug')
                    
                    if not content.strip():
                        self._log_and_print(f"   ⚠️  {self.colors.WARNING}Skipping empty file: {file_path}{self.colors.ENDC}", 'warning')
                        return None
                except Exception as e:
                    self._log_and_print(f"   ❌ {self.colors.FAIL}Error reading file {file_path}: {e}{self.colors.ENDC}", 'error')
                    return None
                
                prompt = f"""
                Analyze this new file: `{file_path}`

                File Content:
                ```
                {content[:2000]}{'...' if len(content) > 2000 else ''}
                ```

                Return ONLY a valid JSON object with:
                1. "summary": Brief description of the file's purpose
                2. "keywords": Array of 2-4 keywords categorizing the change
                3. "feature_area": The main feature/component this file belongs to
                4. "dependencies": Array of file patterns this might depend on
                5. "impact_level": "low", "medium", or "high" based on change significance
                6. "file_type": Type of file (component, utility, config, etc.)

                Example JSON response:
                {{"summary": "New authentication service with JWT handling", "keywords": ["auth", "service", "jwt"], "feature_area": "authentication", "dependencies": ["config", "types"], "impact_level": "medium", "file_type": "service"}}
                """
            else:  # Modified file
                self._log_and_print(f"   📄 Processing modified file...", 'debug')
                
                # First try unstaged changes
                diff_result = self._run_git_command(["git", "diff", "--", file_path], repo_path)
                
                # If no unstaged diff, try staged changes
                if not diff_result or not diff_result.stdout.strip():
                    self._log_and_print(f"   🔍 No unstaged changes, checking staged changes...", 'debug')
                    diff_result = self._run_git_command(["git", "diff", "--cached", "--", file_path], repo_path)
                
                # If still no diff, try diff against HEAD
                if not diff_result or not diff_result.stdout.strip():
                    self._log_and_print(f"   🔍 No staged changes, checking against HEAD...", 'debug')
                    diff_result = self._run_git_command(["git", "diff", "HEAD", "--", file_path], repo_path)
                
                if not diff_result:
                    self._log_and_print(f"   ❌ {self.colors.FAIL}Git diff command failed for {file_path}{self.colors.ENDC}", 'error')
                    return self._create_fallback_analysis(file_path, status)
                
                if not diff_result.stdout.strip():
                    self._log_and_print(f"   ⚠️  {self.colors.WARNING}No diff output found for {file_path}, treating as new file{self.colors.ENDC}", 'warning')
                    # If no diff found, treat as new file and read content
                    try:
                        with open(full_path, 'r', encoding='utf-8', errors='ignore') as f:
                            content = f.read()
                        
                        if not content.strip():
                            self._log_and_print(f"   ⚠️  {self.colors.WARNING}File is empty: {file_path}{self.colors.ENDC}", 'warning')
                            return None
                        
                        self._log_and_print(f"   📝 Reading file content ({len(content)} characters) instead of diff", 'debug')
                        
                        prompt = f"""
                        Analyze this file: `{file_path}` (appears to be new or significantly changed)

                        File Content:
                        ```
                        {content[:2000]}{'...' if len(content) > 2000 else ''}
                        ```

                        Return ONLY a valid JSON object with:
                        1. "summary": Brief description of the file's purpose or changes
                        2. "keywords": Array of 2-4 keywords categorizing the file
                        3. "feature_area": The main feature/component this file belongs to
                        4. "dependencies": Array of file patterns this might depend on
                        5. "impact_level": "low", "medium", or "high" based on change significance
                        6. "change_type": Type of change (new_file, feature, refactor, etc.)

                        Example JSON response:
                        {{"summary": "New React component for partner offerings display", "keywords": ["react", "component", "partner"], "feature_area": "partner_ui", "dependencies": ["react", "typescript"], "impact_level": "medium", "change_type": "new_file"}}
                        """
                    except Exception as e:
                        self._log_and_print(f"   ❌ {self.colors.FAIL}Error reading file content for {file_path}: {e}{self.colors.ENDC}", 'error')
                        return self._create_fallback_analysis(file_path, status)
                else:
                    self._log_and_print(f"   📝 Diff length: {len(diff_result.stdout)} characters", 'debug')
                    
                    prompt = f"""
                    Analyze changes to file: `{file_path}`

                    Git Diff:
                    ```diff
                    {diff_result.stdout[:2000]}{'...' if len(diff_result.stdout) > 2000 else ''}
                    ```

                    Return ONLY a valid JSON object with:
                    1. "summary": Brief description of what changed
                    2. "keywords": Array of 2-4 keywords categorizing the change
                    3. "feature_area": The main feature/component this change affects
                    4. "dependencies": Array of file patterns this might affect
                    5. "impact_level": "low", "medium", or "high" based on change significance
                    6. "change_type": Type of change (bugfix, feature, refactor, etc.)

                    Example JSON response:
                    {{"summary": "Fixed authentication token validation logic", "keywords": ["auth", "bugfix", "validation"], "feature_area": "authentication", "dependencies": ["types", "config"], "impact_level": "medium", "change_type": "bugfix"}}
                    """

            # Generate content with Gemini
            self._log_and_print(f"   🤖 Sending request to Gemini...", 'debug')
            self._log_and_print(f"   📝 Prompt length: {len(prompt)} characters", 'debug')
            
            response = self.gemini_model.generate_content(prompt)
            
            # Validate response
            if not response:
                self._log_and_print(f"   ❌ {self.colors.FAIL}No response from Gemini for {file_path}{self.colors.ENDC}", 'error')
                return self._create_fallback_analysis(file_path, status)
            
            if not response.text:
                self._log_and_print(f"   ❌ {self.colors.FAIL}Empty response text from Gemini for {file_path}{self.colors.ENDC}", 'error')
                return self._create_fallback_analysis(file_path, status)
            
            response_text = response.text.strip()
            self._log_and_print(f"   📤 Gemini response length: {len(response_text)} characters", 'debug')
            self._log_and_print(f"   📝 Response preview: {response_text[:100]}{'...' if len(response_text) > 100 else ''}", 'debug')
            
            # Try to extract JSON from response
            try:
                self._log_and_print(f"   🔧 Attempting direct JSON parsing...", 'debug')
                result = json.loads(response_text)
                self._log_and_print(f"   ✅ Direct JSON parsing successful", 'debug')
            except json.JSONDecodeError as e:
                self._log_and_print(f"   ⚠️  Direct JSON parsing failed: {e}", 'debug')
                self._log_and_print(f"   🔧 Attempting to extract JSON from markdown...", 'debug')
                
                # Try to extract JSON from markdown code blocks
                json_match = re.search(r'```(?:json)?\s*(\{.*?\})\s*```', response_text, re.DOTALL)
                if json_match:
                    try:
                        result = json.loads(json_match.group(1))
                        self._log_and_print(f"   ✅ JSON extracted from markdown", 'debug')
                    except json.JSONDecodeError as e:
                        self._log_and_print(f"   ❌ Failed to parse JSON from markdown: {e}", 'debug')
                        return self._create_fallback_analysis(file_path, status)
                else:
                    self._log_and_print(f"   🔧 Attempting to find JSON-like content...", 'debug')
                    # Try to find JSON-like content
                    json_match = re.search(r'\{.*\}', response_text, re.DOTALL)
                    if json_match:
                        try:
                            result = json.loads(json_match.group(0))
                            self._log_and_print(f"   ✅ JSON extracted from text", 'debug')
                        except json.JSONDecodeError as e:
                            self._log_and_print(f"   ❌ Failed to parse extracted JSON: {e}", 'debug')
                            return self._create_fallback_analysis(file_path, status)
                    else:
                        self._log_and_print(f"   ❌ No JSON found in response", 'debug')
                        return self._create_fallback_analysis(file_path, status)
            
            # Validate required fields
            required_fields = ["summary", "keywords", "feature_area"]
            missing_fields = [field for field in required_fields if field not in result]
            
            if missing_fields:
                self._log_and_print(f"   ❌ {self.colors.FAIL}Missing required fields: {missing_fields}{self.colors.ENDC}", 'error')
                return self._create_fallback_analysis(file_path, status)
            
            self._log_and_print(f"   ✅ All required fields present", 'debug')
            
            # Add file pattern classifications
            result['file_patterns'] = list(self._classify_file_by_pattern(file_path))
            self._log_and_print(f"   🏷️  File patterns: {result['file_patterns']}", 'debug')
            
            self._log_and_print(f"   ✅ {self.colors.OKGREEN}Analysis completed successfully for {file_path}{self.colors.ENDC}")
            return result
            
        except Exception as e:
            self._log_and_print(f"   ❌ {self.colors.FAIL}Unexpected error analyzing {file_path}: {e}{self.colors.ENDC}", 'error')
            self._log_and_print(f"   🔧 Using fallback analysis...", 'debug')
            return self._create_fallback_analysis(file_path, status)

    def _create_fallback_analysis(self, file_path: str, status: str) -> Dict:
        """Creates a fallback analysis when Gemini fails."""
        file_ext = os.path.splitext(file_path)[1].lower()
        file_name = os.path.basename(file_path).lower()
        
        # Basic categorization based on file extension and name
        if file_ext in ['.js', '.jsx', '.ts', '.tsx']:
            keywords = ["javascript", "frontend"]
            feature_area = "frontend"
            file_type = "component" if "component" in file_name else "source"
        elif file_ext in ['.py']:
            keywords = ["python", "backend"]
            feature_area = "backend"
            file_type = "script"
        elif file_ext in ['.css', '.scss', '.sass']:
            keywords = ["styling", "css"]
            feature_area = "styling"
            file_type = "stylesheet"
        elif file_ext in ['.json']:
            keywords = ["config", "json"]
            feature_area = "configuration"
            file_type = "config"
        elif file_ext in ['.md']:
            keywords = ["docs", "markdown"]
            feature_area = "documentation"
            file_type = "documentation"
        else:
            keywords = ["misc"]
            feature_area = "misc"
            file_type = "other"
        
        return {
            "summary": f"{'New' if status == '??' else 'Modified'} {file_type} file: {file_path}",
            "keywords": keywords,
            "feature_area": feature_area,
            "dependencies": [],
            "impact_level": "low",
            "file_type": file_type,
            "file_patterns": list(self._classify_file_by_pattern(file_path))
        }

    def _group_files_by_features(self, file_analyses: List[Dict]) -> Dict[str, List[str]]:
        """Groups files by feature areas and dependencies."""
        feature_groups = defaultdict(list)
        dependency_groups = defaultdict(list)
        
        # Group by feature area
        for analysis in file_analyses:
            feature_area = analysis.get('feature_area', 'misc')
            feature_groups[feature_area].append(analysis['file'])
        
        # Group by dependencies and file patterns
        for analysis in file_analyses:
            file_patterns = analysis.get('file_patterns', [])
            for pattern in file_patterns:
                dependency_groups[pattern].append(analysis['file'])
        
        # Combine similar groups
        final_groups = {}
        
        # Add feature-based groups (prioritize larger groups)
        for feature, files in sorted(feature_groups.items(), key=lambda x: len(x[1]), reverse=True):
            if len(files) > 1:
                final_groups[f"Feature: {feature}"] = files
            else:
                # Try to merge single files with dependency groups
                file = files[0]
                file_analysis = next(a for a in file_analyses if a['file'] == file)
                merged = False
                
                for dep_type, dep_files in dependency_groups.items():
                    if file in dep_files and len(dep_files) > 1:
                        if f"Type: {dep_type}" not in final_groups:
                            final_groups[f"Type: {dep_type}"] = dep_files
                        merged = True
                        break
                
                if not merged:
                    final_groups[f"Individual: {file}"] = [file]
        
        # Add remaining dependency groups
        for dep_type, files in dependency_groups.items():
            group_name = f"Type: {dep_type}"
            if len(files) > 1 and group_name not in final_groups:
                final_groups[group_name] = files
        
        return final_groups

    @retry(tries=3, delay=1, backoff=2)
    def _generate_commit_message_for_group(self, repo_path: str, files: List[str], group_context: str = "") -> Optional[str]:
        """Generates a commit message for a group of files using Gemini."""
        print(f"\n{self.colors.OKBLUE}Generating commit message for {len(files)} file(s)...{self.colors.ENDC}")
        
        # Stage files temporarily to get diff
        self._run_git_command(["git", "add", "--"] + files, repo_path)
        diff_result = self._run_git_command(["git", "diff", "--cached"], repo_path)
        self._run_git_command(["git", "reset", "HEAD", "--"] + files, repo_path)

        if not diff_result or not diff_result.stdout:
            return None

        prompt = f"""
        Generate a conventional commit message for these changes:

        Files: {', '.join(files)}
        Group Context: {group_context}

        Combined Git Diff:
        ```diff
        {diff_result.stdout[:3000]}{'...' if len(diff_result.stdout) > 3000 else ''}
        ```

        Requirements:
        1. Use conventional commit format: type(scope): description
        2. Types: feat, fix, docs, style, refactor, test, chore
        3. Keep description under 50 characters
        4. Add body if needed for complex changes
        5. Consider the group context when determining scope

        Return ONLY the commit message as plain text, no markdown formatting.
        Example: "feat(auth): implement JWT token validation"
        """

        try:
            response = self.gemini_model.generate_content(prompt)
            
            if not response or not response.text:
                print(f"{self.colors.WARNING}Empty response from Gemini for commit message{self.colors.ENDC}")
                return self._create_fallback_commit_message(files, group_context)
            
            # Clean up the response
            commit_message = response.text.strip()
            
            # Remove any markdown formatting
            commit_message = re.sub(r'```[^`]*```', '', commit_message)
            commit_message = re.sub(r'`([^`]+)`', r'\1', commit_message)
            commit_message = commit_message.strip()
            
            # Take only the first line if multiple lines
            commit_message = commit_message.split('\n')[0].strip()
            
            # Validate commit message format
            if not commit_message or len(commit_message) < 10:
                print(f"{self.colors.WARNING}Invalid commit message format, using fallback{self.colors.ENDC}")
                return self._create_fallback_commit_message(files, group_context)
            
            return commit_message
            
        except Exception as e:
            print(f"{self.colors.FAIL}Error generating commit message: {e}{self.colors.ENDC}")
            return self._create_fallback_commit_message(files, group_context)

    def _create_fallback_commit_message(self, files: List[str], group_context: str = "") -> str:
        """Creates a fallback commit message when Gemini fails."""
        if len(files) == 1:
            file_path = files[0]
            file_ext = os.path.splitext(file_path)[1].lower()
            
            if file_ext in ['.js', '.jsx', '.ts', '.tsx']:
                return f"feat(frontend): update {os.path.basename(file_path)}"
            elif file_ext in ['.py']:
                return f"feat(backend): update {os.path.basename(file_path)}"
            elif file_ext in ['.css', '.scss', '.sass']:
                return f"style: update {os.path.basename(file_path)}"
            elif file_ext in ['.json']:
                return f"chore(config): update {os.path.basename(file_path)}"
            elif file_ext in ['.md']:
                return f"docs: update {os.path.basename(file_path)}"
            else:
                return f"chore: update {os.path.basename(file_path)}"
        else:
            # Multiple files
            if "Feature:" in group_context:
                feature = group_context.replace("Feature: ", "").lower()
                return f"feat({feature}): update {len(files)} files"
            elif "Type:" in group_context:
                file_type = group_context.replace("Type: ", "").lower()
                return f"chore({file_type}): update {len(files)} files"
            else:
                return f"chore: update {len(files)} files"

    @retry(tries=3, delay=1, backoff=2)
    def _get_ai_review(self, repo_path: str, files: List[str]) -> Optional[List[str]]:
        """Gets AI code review using Gemini."""
        print(f"\n{self.colors.OKCYAN}Performing AI code review...{self.colors.ENDC}")
        
        self._run_git_command(["git", "add", "--"] + files, repo_path)
        diff_result = self._run_git_command(["git", "diff", "--cached"], repo_path)
        self._run_git_command(["git", "reset", "HEAD", "--"] + files, repo_path)

        if not diff_result or not diff_result.stdout:
            return None

        prompt = f"""
        Perform a code review on these changes:

        Files: {', '.join(files)}

        Git Diff:
        ```diff
        {diff_result.stdout[:3000]}{'...' if len(diff_result.stdout) > 3000 else ''}
        ```

        Review for:
        1. Logic errors or bugs
        2. Security vulnerabilities
        3. Performance issues
        4. Code style and best practices
        5. Missing error handling
        6. Potential side effects

        Return ONLY a JSON array of review comments. If no issues found, return empty array.
        Example: ["Consider adding error handling for API calls", "This function could benefit from input validation"]
        """

        try:
            response = self.gemini_model.generate_content(prompt)
            
            if not response or not response.text:
                print(f"{self.colors.WARNING}Empty response from Gemini for code review{self.colors.ENDC}")
                return []
            
            response_text = response.text.strip()
            
            # Try to extract JSON from response
            try:
                # First try direct JSON parsing
                result = json.loads(response_text)
                if isinstance(result, list):
                    return result
                else:
                    print(f"{self.colors.WARNING}Review response is not a list, using fallback{self.colors.ENDC}")
                    return []
            except json.JSONDecodeError:
                # Try to extract JSON from markdown code blocks
                json_match = re.search(r'```(?:json)?\s*(\[.*?\])\s*```', response_text, re.DOTALL)
                if json_match:
                    try:
                        result = json.loads(json_match.group(1))
                        if isinstance(result, list):
                            return result
                    except json.JSONDecodeError:
                        pass
                
                # Try to find JSON array content
                json_match = re.search(r'\[.*\]', response_text, re.DOTALL)
                if json_match:
                    try:
                        result = json.loads(json_match.group(0))
                        if isinstance(result, list):
                            return result
                    except json.JSONDecodeError:
                        pass
                
                # If no JSON found, try to extract comments from text
                lines = response_text.split('\n')
                comments = []
                for line in lines:
                    line = line.strip()
                    if line and not line.startswith('#') and not line.startswith('*'):
                        # Remove markdown formatting and bullet points
                        line = re.sub(r'^[-*+]\s*', '', line)
                        line = re.sub(r'^\d+\.\s*', '', line)
                        if len(line) > 10:  # Only meaningful comments
                            comments.append(line)
                
                return comments[:5]  # Limit to 5 comments
                
        except Exception as e:
            print(f"{self.colors.FAIL}Error generating AI review: {e}{self.colors.ENDC}")
            return []

    def test(self, file_path: str, repo_path: str = ".") -> None:
        """Generates test skeleton for changes in a specific file."""
        abs_repo_path = os.path.abspath(repo_path)
        self._check_prerequisites(abs_repo_path)

        print(f"Generating test skeleton for: {self.colors.OKCYAN}{file_path}{self.colors.ENDC}")
        
        # Get diff
        diff_result = self._run_git_command(["git", "diff", "HEAD", "--", file_path], abs_repo_path)
        if not diff_result or not diff_result.stdout:
            diff_result = self._run_git_command(["git", "diff", "--", file_path], abs_repo_path)

        if not diff_result or not diff_result.stdout:
            print(f"{self.colors.FAIL}No changes found in {file_path}.{self.colors.ENDC}")
            return

        prompt = f"""
        Generate a comprehensive test skeleton for the changes in `{file_path}`.

        Git Diff:
        ```diff
        {diff_result.stdout}
        ```

        Requirements:
        1. Create test cases for main functionality
        2. Include edge cases and error scenarios
        3. Use appropriate testing framework syntax
        4. Add descriptive test names
        5. Include setup/teardown if needed
        6. Add TODO comments for complex logic

        Return the test code as plain text.
        """

        try:
            response = self.gemini_model.generate_content(prompt)
            print("\n" + "="*50)
            print(f"{self.colors.HEADER}AI-Generated Test Skeleton{self.colors.ENDC}")
            print("="*50)
            print(f"{self.colors.OKGREEN}{response.text}{self.colors.ENDC}")
            print("="*50)
            print(f"{self.colors.WARNING}Note: Review and complete the test logic as needed.{self.colors.ENDC}")
        except Exception as e:
            print(f"{self.colors.FAIL}Error generating test skeleton: {e}{self.colors.ENDC}")

    def summarize(self, base_branch: str = "origin/main", head_branch: Optional[str] = None, repo_path: str = ".") -> None:
        """Generates PR summary using Gemini."""
        abs_repo_path = os.path.abspath(repo_path)
        self._check_prerequisites(abs_repo_path)

        if head_branch is None:
            head_branch_result = self._run_git_command(["git", "rev-parse", "--abbrev-ref", "HEAD"], abs_repo_path)
            head_branch = head_branch_result.stdout.strip()

        print(f"Generating PR summary for '{self.colors.OKCYAN}{head_branch}{self.colors.ENDC}' against '{self.colors.OKCYAN}{base_branch}{self.colors.ENDC}'...")
        
        diff_result = self._run_git_command(["git", "diff", f"{base_branch}..{head_branch}"], abs_repo_path)
        if not diff_result or not diff_result.stdout:
            print(f"{self.colors.WARNING}No differences found.{self.colors.ENDC}")
            return

        prompt = f"""
        Generate a comprehensive Pull Request summary:

        Branch Diff:
        ```diff
        {diff_result.stdout[:4000]}{'...' if len(diff_result.stdout) > 4000 else ''}
        ```

        Create:
        1. **Title**: Concise PR title
        2. **Summary**: High-level overview of changes
        3. **Changes**: Bulleted list of key modifications
        4. **Impact**: Potential effects on the system
        5. **Testing**: Suggested testing approach
        6. **Deployment**: Any deployment considerations

        Format as clean Markdown.
        """

        try:
            response = self.gemini_model.generate_content(prompt)
            print("\n" + "="*60)
            print(f"{self.colors.HEADER}AI-Generated Pull Request Summary{self.colors.ENDC}")
            print("="*60)
            print(f"{self.colors.OKCYAN}{response.text}{self.colors.ENDC}")
            print("="*60)
        except Exception as e:
            print(f"{self.colors.FAIL}Error generating PR summary: {e}{self.colors.ENDC}")

    @retry(tries=3, delay=1, backoff=2)
    def summarize_recent(self, hours: int = 1, repo_path: str = ".") -> None:
        """Summarizes commits from the last N hours using Gemini.
        
        This method analyzes recent git commits and generates a comprehensive development summary
        including statistics, key changes, affected files, and AI-powered insights.
        
        Args:
            hours (int): Number of hours to look back for commits (default: 1)
            repo_path (str): Path to the git repository (default: current directory)
        
        Usage Examples:
            python analysis.py summarize_recent
            python analysis.py summarize_recent --hours=2
            python analysis.py summarize_recent --hours=24 --repo-path='../my-project'
        
        Features:
            - Analyzes commit messages and code changes
            - Generates professional development summaries
            - Provides insights and recommendations
            - Logs summary to file for record keeping
            - Supports team standup and progress reporting
        """
        abs_repo_path = os.path.abspath(repo_path)
        self._check_prerequisites(abs_repo_path)
        
        # Setup logging
        self._setup_logging(abs_repo_path)
        
        print(f"📊 Summarizing commits from the last {self.colors.OKCYAN}{hours} hour{'s' if hours != 1 else ''}{self.colors.ENDC}...")
        
        # Get commits from the last N hours
        since_time = f"{hours} hours ago"
        log_result = self._run_git_command([
            "git", "log", f"--since={since_time}", "--oneline", "--no-merges"
        ], abs_repo_path)
        
        if not log_result or not log_result.stdout.strip():
            print(f"{self.colors.WARNING}No commits found in the last {hours} hour{'s' if hours != 1 else ''}.{self.colors.ENDC}")
            return
        
        commit_lines = log_result.stdout.strip().split('\n')
        commit_count = len(commit_lines)
        
        print(f"📝 Found {self.colors.OKGREEN}{commit_count} commit{'s' if commit_count != 1 else ''}{self.colors.ENDC}")
        
        # Get detailed commit information
        detailed_log_result = self._run_git_command([
            "git", "log", f"--since={since_time}", "--no-merges", "--pretty=format:%h|%an|%ar|%s", "--stat"
        ], abs_repo_path)
        
        if not detailed_log_result or not detailed_log_result.stdout.strip():
            print(f"{self.colors.FAIL}Failed to get detailed commit information.{self.colors.ENDC}")
            return
        
        # Get diff for all commits in the time period - handle edge cases
        diff_content = ""
        try:
            # Try to get diff from the oldest commit in the time period
            if commit_count > 0:
                # Get the oldest commit hash from the time period
                oldest_commit_result = self._run_git_command([
                    "git", "log", f"--since={since_time}", "--no-merges", "--pretty=format:%H", "--reverse"
                ], abs_repo_path)
                
                if oldest_commit_result and oldest_commit_result.stdout.strip():
                    oldest_commit = oldest_commit_result.stdout.strip().split('\n')[0]
                    # Get diff from parent of oldest commit to HEAD
                    diff_result = self._run_git_command([
                        "git", "diff", f"{oldest_commit}^", "HEAD"
                    ], abs_repo_path)
                    
                    if not diff_result:
                        # Fallback: try just the oldest commit to HEAD
                        diff_result = self._run_git_command([
                            "git", "diff", oldest_commit, "HEAD"
                        ], abs_repo_path)
                    
                    if diff_result and diff_result.stdout:
                        diff_content = diff_result.stdout
                    else:
                        # Final fallback: get diff since time period
                        diff_result = self._run_git_command([
                            "git", "diff", f"HEAD@{{{since_time}}}", "HEAD"
                        ], abs_repo_path)
                        diff_content = diff_result.stdout if diff_result else ""
        except Exception as e:
            self._log_and_print(f"Warning: Could not get diff content: {e}", 'warning')
            diff_content = ""
        
        # Prepare the summary data
        commit_info = detailed_log_result.stdout.strip()
        
        # Create comprehensive prompt for Gemini
        prompt = f"""
        Generate a comprehensive development summary for the last {hours} hour{'s' if hours != 1 else ''}:

        **Commit Information:**
        ```
        {commit_info[:3000]}{'...' if len(commit_info) > 3000 else ''}
        ```

        **Code Changes:**
        ```diff
        {diff_content[:2000]}{'...' if len(diff_content) > 2000 else ''}
        ```

        **Analysis Requirements:**
        Create a professional development summary with:

        1. **📊 Summary Statistics**
           - Total commits: {commit_count}
           - Time period: Last {hours} hour{'s' if hours != 1 else ''}
           - Contributors involved

        2. **🎯 Key Developments**
           - Major features or changes implemented
           - Bug fixes and improvements
           - Configuration or infrastructure changes

        3. **📁 Files & Areas Affected**
           - Main components/modules changed
           - File types (frontend, backend, config, etc.)
           - Impact assessment

        4. **🔄 Development Patterns**
           - Types of changes (features, fixes, refactoring)
           - Development activity intensity
           - Any notable patterns

        5. **💡 Insights & Recommendations**
           - Code quality observations
           - Potential areas of concern
           - Suggested next steps

        **Format:** Use clean Markdown with emojis for better readability.
        **Tone:** Professional but engaging, suitable for team updates or standup reports.
        """
        
        try:
            self._log_and_print(f"🤖 Generating AI summary...", 'info')
            response = self.gemini_model.generate_content(prompt)
            
            if not response or not response.text:
                print(f"{self.colors.FAIL}Failed to generate summary from Gemini.{self.colors.ENDC}")
                return
            
            # Display the summary
            print("\n" + "="*80)
            print(f"{self.colors.HEADER}{self.colors.BOLD}🕐 Development Summary - Last {hours} Hour{'s' if hours != 1 else ''}{self.colors.ENDC}")
            print("="*80)
            print(f"{self.colors.OKCYAN}{response.text}{self.colors.ENDC}")
            print("="*80)
            
            # Also save to log file
            self._log_and_print(f"\n{'='*80}")
            self._log_and_print(f"🕐 Development Summary - Last {hours} Hour{'s' if hours != 1 else ''}")
            self._log_and_print(f"{'='*80}")
            self._log_and_print(f"{response.text}")
            self._log_and_print(f"{'='*80}")
            
            print(f"\n{self.colors.OKGREEN}✅ Summary generated successfully!{self.colors.ENDC}")
            print(f"📄 Full summary also saved to log file.")
            
        except Exception as e:
            error_msg = f"Error generating recent commits summary: {e}"
            print(f"{self.colors.FAIL}{error_msg}{self.colors.ENDC}")
            self._log_and_print(error_msg, 'error')

    def commit(self, repo_path: str = ".", skip_reset: bool = False, auto_mode: bool = False, 
               summarize: bool = False, base_branch: str = "origin/main") -> None:
        """Main command for AI-powered commit process with enhanced file batching."""
        abs_repo_path = os.path.abspath(repo_path)
        self._check_prerequisites(abs_repo_path)
        
        # Setup logging
        self._setup_logging(abs_repo_path)
        
        if auto_mode:
            message = f"\n{self.colors.HEADER}{self.colors.BOLD}Enhanced Auto Mode with Smart Batching{self.colors.ENDC}"
            self._log_and_print(message)
            if not questionary.confirm("Auto mode will intelligently group and commit related files. Proceed?", default=False).ask():
                self._log_and_print(f"{self.colors.WARNING}Auto mode cancelled.{self.colors.ENDC}")
                return

        while True:
            if not skip_reset:
                message = f"{self.colors.OKCYAN}Resetting staged files...{self.colors.ENDC}"
                self._log_and_print(message)
                self._run_git_command(["git", "reset"], abs_repo_path)
            
            # Auto-commit deleted files and dependencies
            self._auto_commit_deleted_files(abs_repo_path)
            all_changed_files = self._get_changed_files(abs_repo_path)
            self._auto_commit_dependency_updates(abs_repo_path, [f['file'] for f in all_changed_files])
            self._auto_commit_image_files(abs_repo_path, [f['file'] for f in all_changed_files])
            
            # Get remaining files to analyze
            remaining_files = self._get_changed_files(abs_repo_path)
            if not remaining_files:
                message = f"\n{self.colors.OKGREEN}All changes committed successfully!{self.colors.ENDC}"
                self._log_and_print(message)
                break

            message = f"\n{self.colors.HEADER}Analyzing {len(remaining_files)} file(s) with Gemini...{self.colors.ENDC}"
            self._log_and_print(message)
            
            # Log all files to be analyzed
            self._log_and_print(f"📋 Files queued for analysis:")
            for i, file_info in enumerate(remaining_files, 1):
                self._log_and_print(f"   {i}. {file_info['file']} (status: {file_info['status']})")
            
            # Analyze files with Gemini
            file_analyses = []
            successful_analyses = 0
            failed_analyses = 0
            
            for file_info in remaining_files:
                self._log_and_print(f"\n{'='*60}")
                analysis = self._analyze_single_file(abs_repo_path, file_info)
                if analysis and "summary" in analysis:
                    analysis['file'] = file_info['file']
                    file_analyses.append(analysis)
                    successful_analyses += 1
                    self._log_and_print(f"✅ Analysis successful for: {file_info['file']}")
                else:
                    failed_analyses += 1
                    self._log_and_print(f"❌ Analysis failed for: {file_info['file']}", 'error')

            self._log_and_print(f"\n{'='*60}")
            self._log_and_print(f"📊 Analysis Summary:")
            self._log_and_print(f"   ✅ Successful: {successful_analyses}")
            self._log_and_print(f"   ❌ Failed: {failed_analyses}")
            self._log_and_print(f"   📝 Total analyzed: {len(file_analyses)}")

            if not file_analyses:
                self._log_and_print(f"\n{self.colors.WARNING}No files could be analyzed. Reasons could be:", 'warning')
                self._log_and_print(f"   • Files don't exist in the working directory", 'warning')
                self._log_and_print(f"   • Files are empty or unreadable", 'warning')
                self._log_and_print(f"   • Git diff failed to generate output", 'warning')
                self._log_and_print(f"   • Gemini API is not responding", 'warning')
                self._log_and_print(f"   • API key issues", 'warning')
                self._log_and_print(f"   • All responses failed JSON validation", 'warning')
                self._log_and_print(f"Exiting.{self.colors.ENDC}", 'warning')
                break

            # Group files intelligently
            file_groups = self._group_files_by_features(file_analyses)
            
            if auto_mode:
                # Auto-commit the largest/most logical group first
                sorted_groups = sorted(file_groups.items(), key=lambda x: len(x[1]), reverse=True)
                
                for group_name, group_files in sorted_groups:
                    if group_files:  # Check if group still has uncommitted files
                        message = f"\n{self.colors.OKBLUE}Auto-committing group: {group_name} ({len(group_files)} files){self.colors.ENDC}"
                        self._log_and_print(message)
                        
                        commit_message = self._generate_commit_message_for_group(abs_repo_path, group_files, group_name)
                        if commit_message:
                            self._log_and_print(f"{self.colors.OKGREEN}Generated message: {commit_message}{self.colors.ENDC}")
                            if self._commit_files(abs_repo_path, group_files, commit_message):
                                break
                        else:
                            self._log_and_print(f"{self.colors.FAIL}Failed to generate commit message for group.{self.colors.ENDC}", 'error')
                            continue
                continue

            # Interactive mode with enhanced grouping
            remaining_files_to_commit = {analysis['file'] for analysis in file_analyses}
            
            while remaining_files_to_commit:
                choices = []
                
                # Add intelligent groups
                for group_name, group_files in file_groups.items():
                    available_files = [f for f in group_files if f in remaining_files_to_commit]
                    if len(available_files) > 1:
                        choices.append(questionary.Choice(
                            title=f"{group_name}: {len(available_files)} files",
                            value={"type": "smart_group", "files": available_files, "context": group_name}
                        ))
                
                # Add individual files
                individual_files = [f for f in remaining_files_to_commit 
                                  if not any(f in group_files for group_files in file_groups.values() if len(group_files) > 1)]
                
                if individual_files:
                    choices.append(questionary.Separator())
                    choices.append(questionary.Choice(
                        title="Select individual files...",
                        value={"type": "manual", "files": individual_files}
                    ))
                
                choices.extend([
                    questionary.Separator(),
                    questionary.Choice(title="Exit", value={"type": "exit"})
                ])

                selection = questionary.select("Choose files to commit:", choices=choices).ask()
                
                if not selection or selection['type'] == 'exit':
                    self._log_and_print(f"{self.colors.WARNING}Exiting commit session.{self.colors.ENDC}")
                    return

                selected_files = []
                context = ""
                
                if selection['type'] == 'smart_group':
                    selected_files = selection['files']
                    context = selection['context']
                elif selection['type'] == 'manual':
                    manual_selection = questionary.checkbox(
                        'Select files to commit together:',
                        choices=selection['files']
                    ).ask()
                    if manual_selection:
                        selected_files = manual_selection
                        context = "Manual selection"

                if not selected_files:
                    continue

                # Generate commit message
                commit_message = self._generate_commit_message_for_group(abs_repo_path, selected_files, context)
                if not commit_message:
                    self._log_and_print(f"{self.colors.WARNING}Failed to generate commit message.{self.colors.ENDC}", 'warning')
                    continue

                # Display commit details
                self._log_and_print(f"\n{self.colors.OKGREEN}Proposed Commit:{self.colors.ENDC}")
                self._log_and_print(f"{self.colors.BOLD}{commit_message}{self.colors.ENDC}")
                self._log_and_print(f"\n{self.colors.OKBLUE}Files ({len(selected_files)}):{self.colors.ENDC}")
                for f in selected_files:
                    self._log_and_print(f"  • {f}")
                self._log_and_print("-" * 50)

                # Get user action
                action = questionary.select(
                    "What would you like to do?",
                    choices=["Commit", "Edit Message", "Get AI Review", "Skip"]
                ).ask()

                if action == "Commit":
                    if self._commit_files(abs_repo_path, selected_files, commit_message):
                        remaining_files_to_commit.difference_update(selected_files)
                elif action == "Edit Message":
                    edited_message = questionary.text("Edit commit message:", default=commit_message).ask()
                    if edited_message and self._commit_files(abs_repo_path, selected_files, edited_message):
                        remaining_files_to_commit.difference_update(selected_files)
                elif action == "Get AI Review":
                    review_comments = self._get_ai_review(abs_repo_path, selected_files)
                    if review_comments:
                        self._log_and_print(f"\n{self.colors.HEADER}AI Review Comments:{self.colors.ENDC}")
                        for comment in review_comments:
                            self._log_and_print(f"  • {self.colors.WARNING}{comment}{self.colors.ENDC}")
                        self._log_and_print("")
                    else:
                        self._log_and_print(f"\n{self.colors.OKGREEN}No issues found in AI review!{self.colors.ENDC}")
                # Skip - do nothing, continue loop
                
            break  # Exit main loop when all files are committed
        
        if summarize:
            self._log_and_print(f"\n{self.colors.HEADER}Generating post-commit summary...{self.colors.ENDC}")
            self.summarize(base_branch=base_branch, repo_path=abs_repo_path)
        
        if self.logger:
            self.logger.info("=== Git AI Committer Session Completed ===")


if __name__ == "__main__":
    fire.Fire(EnhancedGitAICommitter)
