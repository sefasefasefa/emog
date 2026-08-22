Param(
    [string]$RepoName = (Split-Path -Leaf (Get-Location)),
    [ValidateSet('private','public')]
    [string]$Visibility = 'private'
)

Write-Output "Creating GitHub repo '$RepoName' ($Visibility)"

if (Get-Command gh -ErrorAction SilentlyContinue) {
    Write-Output "Using GitHub CLI (gh) to create repo..."
    gh repo create $RepoName --$Visibility --confirm
    Write-Output "Repository created via gh"
    exit 0
}

$token = $env:GITHUB_TOKEN
if (-not $token) {
    Write-Error "GitHub CLI not found and GITHUB_TOKEN not set. Install gh or set GITHUB_TOKEN env var."
    exit 1
}

$body = @{ name = $RepoName; private = ($Visibility -eq 'private') } | ConvertTo-Json

try {
    $resp = Invoke-RestMethod -Method Post -Uri https://api.github.com/user/repos -Headers @{ Authorization = "token $token"; 'User-Agent' = 'emog-app' } -Body $body -ContentType 'application/json'
    Write-Output "Created: $($resp.html_url)"
} catch {
    Write-Error "Failed to create repo: $_"
    exit 1
}
