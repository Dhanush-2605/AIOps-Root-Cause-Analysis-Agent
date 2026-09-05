import os
import datetime
from github import Github, GithubException
from dotenv import load_dotenv

load_dotenv()

def commit_post_mortem_to_repo(
    post_mortem_content: str, 
    alert_name: str, 
    target_resource: str
) -> str:
    
    """Commits the Post-Mortem Markdown file directly to the repository's default branch."""
    github_token = os.getenv("GITHUB_TOKEN")
    repo_name = os.getenv("GITHUB_REPO") 
    print(github_token, repo_name)
    
    if not github_token or not repo_name:
        return "GitHub integration skipped: GITHUB_TOKEN or GITHUB_REPO not configured in environment."

    try:
        g = Github(github_token)
        repo = g.get_repo(repo_name)
        
        # 1. Format file path & commit message
        timestamp = datetime.datetime.utcnow().strftime("%Y%m%d-%H%M%S")
        clean_alert = alert_name.lower().replace(" ", "-")
        file_path = f"incidents/{timestamp}-{clean_alert}.md"
        
        commit_message = f"docs(incident): Add automated post-mortem for {alert_name} on {target_resource}"
        
        # 2. Commit directly to the repository's default branch (usually 'main' or 'master')
        result = repo.create_file(
            path=file_path,
            message=commit_message,
            content=post_mortem_content,
            branch=repo.default_branch
        )
        
        commit_url = result["commit"].html_url
        print(f"✅ Post-Mortem successfully committed: {commit_url}")
        return commit_url

    except GithubException as e:
        print(f"❌ GitHub API Error: {e}")
        return f"Failed to commit to repository: {str(e)}"
    


print(commit_post_mortem_to_repo("# Sample Post-Mortem\n\nThis is a sample post-mortem content for testing purposes.", "Sample Alert", "Sample Resource"))