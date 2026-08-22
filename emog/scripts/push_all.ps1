Param(
    [string]$RemoteUrl
)

if (-not (Test-Path .git)) {
    git init
    Write-Output "Initialized git repository"
}

git add -A
try { git commit -m "Initial commit" -q } catch { Write-Output "No changes to commit or commit failed: $_" }

if (-not $RemoteUrl) {
    Write-Output "No remote URL provided. If you used scripts/create_github_repo.ps1, pass the repo URL to this script like:` .\scripts\push_all.ps1 https://github.com/you/repo.git`"
    exit 1
}

try {
    git remote remove origin -ErrorAction SilentlyContinue
} catch {}

git remote add origin $RemoteUrl
git branch -M main
git push -u origin main --force

Write-Output "Pushed to $RemoteUrl"
