import os
import subprocess
import requests # A common library for making HTTP requests

# --- Configuration ---
# Replace with the actual API endpoint for your language model
API_ENDPOINT = "http://localhost:11434/api/generate" 
# Replace with your actual API key if required by the provider
API_KEY = "YOUR_API_KEY_HERE" 

def get_staged_diff():
    """
    Retrieves the diff of currently staged files using Git.

    Returns:
        str: The git diff output as a string, or None if an error occurs.
    """
    try:
        # This command shows the differences for files that are staged for the next commit
        result = subprocess.run(
            ["git", "diff", "--staged"],
            capture_output=True,
            text=True,
            check=True
        )
        return result.stdout
    except FileNotFoundError:
        print("Error: 'git' command not found. Is Git installed and in your PATH?")
        return None
    except subprocess.CalledProcessError as e:
        # This can happen if there are no staged changes
        if not e.stdout and not e.stderr:
            print("No staged changes found. Use 'git add' to stage your files.")
            return None
        print(f"An error occurred while running git diff: {e.stderr}")
        return None

def generate_commit_prompt(diff_text):
    """
    Creates the full prompt to be sent to the AI model.

    Args:
        diff_text (str): The git diff output.

    Returns:
        str: The complete, formatted prompt.
    """
    # This is the detailed prompt designed to guide the AI model
    return f"""
You are an expert software developer with a keen eye for writing clean, concise, and informative Git commit messages. Your task is to analyze the following `git diff` output for multiple files and generate a conventional commit message.

**Analyze the following `git diff`:**

```diff
{diff_text}
```

**Based on your analysis, provide a commit message that adheres to the following best practices:**

* **Follow the Conventional Commits specification.** The commit type must be one of the following: `feat`, `fix`, `docs`, `style`, `refactor`, `perf`, `test`, or `chore`.
* **Write a concise and imperative subject line** of no more than 50 characters. The subject line should clearly summarize the change.
* **Provide a detailed and informative body** that explains the "what" and "why" of the changes. You can use bullet points for clarity.
* **Do not include the "how"** in the commit message body; the code itself documents the implementation details.
* **If the changes are significant, suggest breaking them down into smaller, more atomic commits** and provide a suggested commit message for each logical change.

**Output Format:**

**Suggested Commit Message:**
```
<type>(<scope>): <subject>

<blank line>

<Body of the commit message>
```

**Analysis of Changes:**
* [Provide a brief, high-level summary of the changes you identified.]
* [If applicable, suggest how the changes could be split into smaller commits.]

Please begin your response now.
"""

def get_ai_suggestion(prompt):
    """
    Sends the prompt to the AI model and returns the suggestion.
    
    NOTE: This is a placeholder function. You will need to replace the
          contents with the actual API call logic for your AI provider.

    Args:
        prompt (str): The full prompt for the AI.

    Returns:
        str: The AI-generated commit message suggestion.
    """
    headers = {
        "Authorization": f"Bearer {API_KEY}",
        "Content-Type": "application/json",
    }
    
    # The payload will vary depending on the AI provider's API
    payload = {
        "model": "deepseek-r1-7b", # Or your preferred model
        "prompt": prompt,
        "max_tokens": 300, # Adjust as needed
        "temperature": 0.5,
    }
    
    try:
        # In a real application, you would make the API call here
        # response = requests.post(API_ENDPOINT, headers=headers, json=payload)
        # response.raise_for_status()  # Raises an exception for bad status codes
        # return response.json()['choices'][0]['text'].strip()

        # --- Placeholder Response ---
        # For demonstration purposes, we return a hardcoded example response.
        # Replace this with the actual API call above.
        print("\n--- Sending request to AI (mocked) ---")
        return """
**Suggested Commit Message:**
```
feat(auth): implement user registration endpoint

- Add a new POST endpoint `/api/register` to handle user creation.
- Hash passwords using bcrypt before storing them in the database.
- Implement validation to check for existing email addresses to prevent duplicates.
```

**Analysis of Changes:**
* This change introduces a new feature for user registration, including API logic, password security, and data validation.
* The changes are cohesive and represent a single logical feature, so splitting the commit is not necessary.
        """

    except requests.exceptions.RequestException as e:
        return f"Error connecting to the AI service: {e}"


def main():
    """
    Main function to run the commit message generator.
    """
    print("Analyzing staged files for commit message suggestion...")
    diff = get_staged_diff()

    if diff:
        prompt = generate_commit_prompt(diff)
        suggestion = get_ai_suggestion(prompt)
        print("\n================ AI Suggestion ================")
        print(suggestion)
        print("=============================================")
        
        # You could add logic here to automatically apply the commit message
        # e.g., subprocess.run(["git", "commit", "-m", parsed_message])

if __name__ == "__main__":
    main()
