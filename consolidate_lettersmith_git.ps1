param(
    [string]$ProjectPath = "C:\Users\Oluwatola Ayedun\Desktop\SmithLetter",
    [string]$RemoteUrl = "https://github.com/TheTola/TheLetterSmith.git",
    [string]$CommitMessage = "Save current Letter Smith checkpoint"
)

$ErrorActionPreference = "Stop"

function Run-Git {
    param(
        [Parameter(Mandatory = $true)]
        [string[]]$Arguments,
        [switch]$AllowFailure
    )

    & git -C $ProjectPath @Arguments
    $code = $LASTEXITCODE

    if (-not $AllowFailure -and $code -ne 0) {
        throw "Git command failed: git $($Arguments -join ' ')"
    }

    return $code
}

if (-not (Test-Path -LiteralPath $ProjectPath)) {
    throw "Project folder not found: $ProjectPath"
}

Write-Host "Project: $ProjectPath"
Write-Host "Remote:  $RemoteUrl"
Write-Host ""

# Initialize Git when the folder is not already a repository.
if (-not (Test-Path -LiteralPath (Join-Path $ProjectPath ".git"))) {
    & git -C $ProjectPath init
    if ($LASTEXITCODE -ne 0) {
        throw "Could not initialize the Git repository."
    }
}

# Ensure this project points to the intended Letter Smith repository.
& git -C $ProjectPath remote get-url origin *> $null
if ($LASTEXITCODE -eq 0) {
    $currentRemote = (& git -C $ProjectPath remote get-url origin).Trim()
    if ($currentRemote -ne $RemoteUrl) {
        Write-Host "Changing origin from:"
        Write-Host "  $currentRemote"
        Write-Host "to:"
        Write-Host "  $RemoteUrl"
        Run-Git -Arguments @("remote", "set-url", "origin", $RemoteUrl) | Out-Null
    }
}
else {
    Run-Git -Arguments @("remote", "add", "origin", $RemoteUrl) | Out-Null
}

# Save every current working-tree change before branch consolidation.
Run-Git -Arguments @("add", "-A") | Out-Null
& git -C $ProjectPath diff --cached --quiet
if ($LASTEXITCODE -ne 0) {
    Run-Git -Arguments @("commit", "-m", $CommitMessage) | Out-Null
}
else {
    Write-Host "No uncommitted changes needed a new checkpoint commit."
}

$currentBranch = (& git -C $ProjectPath branch --show-current).Trim()
$localBranchesBefore = @(
    & git -C $ProjectPath for-each-ref --format="%(refname:short)" refs/heads
) | Where-Object { $_ -and $_.Trim() }

$mainExists = $localBranchesBefore -contains "main"

if (-not $mainExists) {
    if ($currentBranch) {
        Run-Git -Arguments @("branch", "-M", "main") | Out-Null
    }
    else {
        Run-Git -Arguments @("switch", "-c", "main") | Out-Null
    }
}
elseif ($currentBranch -ne "main") {
    Run-Git -Arguments @("switch", "main") | Out-Null
}

# Refresh remote branch information. This also works when the remote is empty.
Run-Git -Arguments @("fetch", "origin", "--prune") | Out-Null

# Merge every local branch into main so no local branch has unique commits.
$localBranches = @(
    & git -C $ProjectPath for-each-ref --format="%(refname:short)" refs/heads
) | Where-Object { $_ -and $_.Trim() -and $_ -ne "main" }

foreach ($branch in $localBranches) {
    Write-Host "Merging local branch: $branch"
    Run-Git -Arguments @(
        "merge",
        "--no-edit",
        "--no-ff",
        "--allow-unrelated-histories",
        $branch
    ) | Out-Null
}

# Merge every non-main remote branch into main as well.
$remoteTrackingBranches = @(
    & git -C $ProjectPath for-each-ref --format="%(refname:short)" refs/remotes/origin
) | Where-Object {
    $_ -and
    $_.Trim() -and
    $_ -ne "origin/HEAD" -and
    $_ -ne "origin/main"
}

foreach ($branch in $remoteTrackingBranches) {
    Write-Host "Merging remote branch: $branch"
    Run-Git -Arguments @(
        "merge",
        "--no-edit",
        "--no-ff",
        "--allow-unrelated-histories",
        $branch
    ) | Out-Null
}

# Refuse cleanup unless main contains every branch's history.
foreach ($branch in $localBranches) {
    & git -C $ProjectPath merge-base --is-ancestor $branch main
    if ($LASTEXITCODE -ne 0) {
        throw "Safety check failed: main does not contain local branch '$branch'. No branches were deleted."
    }
}

foreach ($branch in $remoteTrackingBranches) {
    & git -C $ProjectPath merge-base --is-ancestor $branch main
    if ($LASTEXITCODE -ne 0) {
        throw "Safety check failed: main does not contain remote branch '$branch'. No branches were deleted."
    }
}

# Publish the consolidated, newest main branch.
Run-Git -Arguments @("push", "-u", "origin", "main") | Out-Null

# Delete all remote branches except main.
$remoteHeads = @(& git -C $ProjectPath ls-remote --heads origin)
$remoteBranchNames = foreach ($line in $remoteHeads) {
    if ($line -match "refs/heads/(.+)$") {
        $Matches[1]
    }
}

foreach ($branch in ($remoteBranchNames | Where-Object { $_ -and $_ -ne "main" })) {
    Write-Host "Deleting remote branch: $branch"
    Run-Git -Arguments @("push", "origin", "--delete", $branch) | Out-Null
}

# Delete all local branches except main.
$localBranchesAfterMerge = @(
    & git -C $ProjectPath for-each-ref --format="%(refname:short)" refs/heads
) | Where-Object { $_ -and $_.Trim() -and $_ -ne "main" }

foreach ($branch in $localBranchesAfterMerge) {
    Write-Host "Deleting local branch: $branch"
    Run-Git -Arguments @("branch", "-D", $branch) | Out-Null
}

Run-Git -Arguments @("fetch", "origin", "--prune") | Out-Null
Run-Git -Arguments @("push", "origin", "main") | Out-Null

# Final verification.
$finalLocalBranches = @(
    & git -C $ProjectPath for-each-ref --format="%(refname:short)" refs/heads
) | Where-Object { $_ -and $_.Trim() }

$finalRemoteHeads = @(& git -C $ProjectPath ls-remote --heads origin)
$finalRemoteBranches = foreach ($line in $finalRemoteHeads) {
    if ($line -match "refs/heads/(.+)$") {
        $Matches[1]
    }
}

$workingTree = @(& git -C $ProjectPath status --porcelain)

if (($finalLocalBranches -join ",") -ne "main") {
    throw "Final verification failed. Local branches: $($finalLocalBranches -join ', ')"
}

if (($finalRemoteBranches -join ",") -ne "main") {
    throw "Final verification failed. Remote branches: $($finalRemoteBranches -join ', ')"
}

if ($workingTree.Count -gt 0) {
    throw "Final verification failed: the working tree still contains uncommitted changes."
}

$latestCommit = (& git -C $ProjectPath log -1 --oneline).Trim()

Write-Host ""
Write-Host "Completed successfully."
Write-Host "Local branches:  main"
Write-Host "Remote branches: main"
Write-Host "Latest commit:   $latestCommit"
Write-Host "Working tree:    clean"
