<#
.SYNOPSIS
  Runs one bounded Claude Code (claude -p) request billed to the Anthropic API key. Windows PowerShell 5.1 compatible.

.DESCRIPTION
  STATUS: parser, ReadPaths refusals and live no-tools/read-path smoke passed 2026-10-09; cancellation/ReadWrite transport tests not repeated.
  - Always runs in the project root (parent of this script's folder), never in the caller's cwd.
  - API key: process ANTHROPIC_API_KEY, else existing User-level ANTHROPIC_API_KEY. Absent -> fail closed.
    The key is set only in the child process environment; never in arguments, settings, logs or output.
  - Default tools: none; approved files are supplied inline with -ReadPaths. -ReadWrite additionally allows Edit/Write on explicit -OwnPaths files only.
  - Permission rules are NOT an OS sandbox. This is not a security boundary.

.EXAMPLE
  powershell -File scripts\Invoke-ClaudeApiAgent.ps1 -Prompt "Reply exactly API_AGENT_OK"
.EXAMPLE
  powershell -File scripts\Invoke-ClaudeApiAgent.ps1 -Prompt "Fix typo in notes" -ReadWrite -OwnPaths docs/notes.md
#>
[CmdletBinding(DefaultParameterSetName = 'Inline')]
param(
    [Parameter(Mandatory = $true, ParameterSetName = 'Inline')]
    [Parameter(Mandatory = $true, ParameterSetName = 'InlineRW')]
    [ValidateNotNullOrEmpty()]
    [string]$Prompt,

    [Parameter(Mandatory = $true, ParameterSetName = 'File')]
    [Parameter(Mandatory = $true, ParameterSetName = 'FileRW')]
    [ValidateNotNullOrEmpty()]
    [string]$PromptFile,

    [ValidateSet('haiku', 'sonnet', 'opus')]
    [string]$Model = 'sonnet',

    [ValidateRange(0.01, 20)]
    [decimal]$MaxBudgetUsd = 1,

    [ValidateRange(1, 100)]
    [int]$MaxTurns = 12,

    [ValidateRange(30, 3600)]
    [int]$TimeoutSeconds = 600,

    [Parameter(Mandatory = $true, ParameterSetName = 'InlineRW')]
    [Parameter(Mandatory = $true, ParameterSetName = 'FileRW')]
    [switch]$ReadWrite,

    [Parameter(Mandatory = $true, ParameterSetName = 'InlineRW')]
    [Parameter(Mandatory = $true, ParameterSetName = 'FileRW')]
    [ValidateNotNullOrEmpty()]
    [string[]]$OwnPaths,

    [Parameter(ParameterSetName = 'Inline')]
    [Parameter(ParameterSetName = 'File')]
    [Parameter(ParameterSetName = 'InlineRW')]
    [Parameter(ParameterSetName = 'FileRW')]
    [ValidateNotNullOrEmpty()]
    [string[]]$ReadPaths
)

Set-StrictMode -Version 2
$ErrorActionPreference = 'Stop'

function ConvertTo-WinArg([string]$Arg) {
    # CommandLineToArgvW-compatible quoting for ProcessStartInfo.Arguments (PS 5.1 has no ArgumentList).
    if ($Arg.Length -eq 0) { return '""' }
    if ($Arg -notmatch '[\s"]') { return $Arg }
    $s = [regex]::Replace($Arg, '(\\*)"', '$1$1\"')
    $s = [regex]::Replace($s, '(\\+)$', '$1$1')
    return '"' + $s + '"'
}

function Get-ClaudeExe {
    # Do not trust PATH ordering: the child receives the API key.  This is the
    # supported per-user install location; version updates keep this path.
    $fallback = Join-Path $env:USERPROFILE '.local\bin\claude.exe'
    if (Test-Path -LiteralPath $fallback -PathType Leaf) { return $fallback }
    throw 'claude.exe not found at the pinned per-user install path. Not installing anything.'
}

function Get-ApiKey {
    $k = $env:ANTHROPIC_API_KEY
    if ([string]::IsNullOrWhiteSpace($k)) {
        $k = [Environment]::GetEnvironmentVariable('ANTHROPIC_API_KEY', 'User')
    }
    if ([string]::IsNullOrWhiteSpace($k)) {
        throw 'ANTHROPIC_API_KEY not available (process or User environment). Failing closed.'
    }
    return $k.Trim()
}

function Resolve-SafeRelativeFile([string]$Root, [string]$Raw, [string]$Label, [switch]$MustExist, [switch]$AllowAbsoluteInsideRoot) {
    # Shared validator for OwnPaths and PromptFile. Returns a root-relative path with '/' separators.
    # Not an OS sandbox: rejects unsafe syntax, forbidden locations and reparse-point ancestors.
    $p = $Raw.Trim().Replace('\', '/')
    if ($AllowAbsoluteInsideRoot -and [IO.Path]::IsPathRooted($p.Replace('/', '\'))) {
        if ($p -cnotmatch '^[A-Za-z0-9_./: -]+\z') { throw "$Label rejected (unsafe characters)." }
        $abs = [IO.Path]::GetFullPath($p.Replace('/', '\'))
        $pre = $Root.TrimEnd('\') + '\'
        if (-not $abs.StartsWith($pre, [StringComparison]::OrdinalIgnoreCase)) { throw "$Label must be inside the project root." }
        $p = $abs.Substring($pre.Length).Replace('\', '/')
    }
    while ($p.StartsWith('./')) { $p = $p.Substring(2) }
    if ($p.Length -eq 0) { throw "$Label is empty." }
    # whitelist: ASCII letters, digits, underscore, dash, dot, slash, space (case-sensitive ranges; \z, not $)
    if ($p -cnotmatch '^[A-Za-z0-9_./ -]+\z') {
        throw "$Label rejected (only ASCII letters, digits, _ - . / and space allowed; no control chars, parentheses, wildcards, unicode)."
    }
    if ($p.StartsWith('/') -or $p.EndsWith('/')) { throw "$Label rejected (absolute or directory path)." }
    $cur = $Root.TrimEnd('\')
    foreach ($seg in $p.Split('/')) {
        if ($seg -eq '' -or $seg -eq '.' -or $seg -eq '..' -or $seg.Trim() -ne $seg -or $seg.EndsWith('.')) {
            throw "$Label rejected (traversal/empty segment)."
        }
        if ($seg -match '^(?i)(\.git|\.claude|\.codex|config|configs|deploy|deployment|deployments|private|secrets?|credentials?)$' -or
            $seg -match '(?i)(^\.env|credential|secret|id_rsa|id_ed25519|id_ecdsa|private[-_.]?key)' -or
            $seg -match '(?i)\.(pem|key|pfx|p12|ppk|jks|keystore)$' -or
            $seg -match '^(?i)(con|prn|aux|nul|com[0-9]|lpt[0-9])(\..*)?$') {
            throw "$Label rejected (forbidden location)."
        }
        # reject reparse points (symlink/junction) on every existing component from root down
        $cur = $cur + '\' + $seg
        if (Test-Path -LiteralPath $cur) {
            $attr = [IO.File]::GetAttributes($cur)
            if (($attr -band [IO.FileAttributes]::ReparsePoint) -ne 0) { throw "$Label rejected (reparse point/junction/symlink in path)." }
        }
    }
    $full = [IO.Path]::GetFullPath((Join-Path $Root $p.Replace('/', '\')))
    $prefix = $Root.TrimEnd('\') + '\'
    if (-not $full.StartsWith($prefix, [StringComparison]::OrdinalIgnoreCase)) { throw "$Label escapes project root." }
    if (Test-Path -LiteralPath $full -PathType Container) { throw "$Label must be an explicit file, not a directory." }
    if ($MustExist -and -not (Test-Path -LiteralPath $full -PathType Leaf)) { throw "$Label file not found." }
    return $p
}

function Read-Context([string]$Path, [string]$Label) {
    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) { throw "Required rules file missing: $Label" }
    $t = [IO.File]::ReadAllText($Path, [Text.Encoding]::UTF8)
    return "----- BEGIN $Label -----`n$t`n----- END $Label -----"
}

function Limit([string]$s, [int]$n) { if ($s.Length -gt $n) { return $s.Substring(0, $n) + '...[truncated]' } return $s }

$apiKey = $null
$proc = $null
$kp = $null
$exitCode = 1
try {
    $root = Split-Path -Parent $PSScriptRoot
    if (-not (Test-Path -LiteralPath $root -PathType Container)) { throw 'Project root not found.' }
    $root = (Resolve-Path -LiteralPath $root).ProviderPath

    # --- prompt ---
    if ($PSCmdlet.ParameterSetName -like 'File*') {
        $pfBase = if ([IO.Path]::IsPathRooted($PromptFile)) { $PromptFile } else { Join-Path (Get-Location).ProviderPath $PromptFile }
        # relative PromptFile is resolved against caller cwd, then must lie inside the project root
        $pfRel = Resolve-SafeRelativeFile $root ([IO.Path]::GetFullPath($pfBase)) 'PromptFile' -MustExist -AllowAbsoluteInsideRoot
        $userPrompt = [IO.File]::ReadAllText((Join-Path $root $pfRel.Replace('/', '\')), [Text.Encoding]::UTF8)
    } else {
        $userPrompt = $Prompt
    }
    if ([string]::IsNullOrWhiteSpace($userPrompt)) { throw 'Prompt is empty.' }

    # --- own paths ---
    $own = @()
    $read = @()
    if ($ReadPaths) {
        foreach ($r in $ReadPaths) {
            if (-not [string]::IsNullOrWhiteSpace($r)) {
                $read += (Resolve-SafeRelativeFile $root $r 'ReadPaths entry' -MustExist)
            }
        }
    }
    $read = @($read | Select-Object -Unique)
    if ($ReadWrite) {
        foreach ($o in $OwnPaths) { $own += (Resolve-SafeRelativeFile $root $o 'OwnPaths entry') }
        $own = @($own | Select-Object -Unique)
        if ($own.Count -eq 0) { throw 'No valid OwnPaths.' }
    }

    # --- rules context (read in-process; agent is not asked to read outside cwd) ---
    $ctx = @()
    $ctx += Read-Context (Join-Path $root 'AGENTS.md') 'AGENTS.md'
    $ctx += Read-Context (Join-Path $root 'CLAUDE.md') 'CLAUDE.md'
    $ctx += Read-Context 'C:\Users\vadim\Projects\AGENT_RULES_motor_ai_sim.md' 'AGENT_RULES_motor_ai_sim.md'
    $ctx += Read-Context 'C:\Users\vadim\Projects\AGENT_DATASET_RULES.md' 'AGENT_DATASET_RULES.md'
    # Only explicitly owned existing files are admitted to the inline context.
    # New write targets remain absent until the agent creates them.
    foreach ($o in $own) {
        $op = Join-Path $root $o.Replace('/', '\')
        if (Test-Path -LiteralPath $op -PathType Leaf) {
            $ctx += Read-Context $op $o
        }
    }
    foreach ($r in $read) {
        $rp = Join-Path $root $r.Replace('/', '\')
        $ctx += Read-Context $rp $r
    }

    if ($ReadWrite) {
        $ownText = "You may edit/write ONLY these files (relative to project root): " + ($own -join ', ') + ". Everything else is read-only."
    } else {
        $ownText = 'This run is READ-ONLY. You own no paths; do not modify anything.'
    }
    $shared = @(
        'SHARED-WORKSPACE CONSTRAINTS (mandatory):',
        '- The working tree is shared and the branch is dirty; other agents work in parallel. Do not touch files you do not own; never revert or clean others'' changes.',
        "- Path ownership for this task: $ownText",
        '- Local API on port 8001: GET requests only (and you have no shell anyway).',
        '- No deploy, no FEM runs/grants, no git operations, no ERP, no Google Drive, no secrets/credentials/.env access.',
        '- Do not spawn nested agents or sub-agents.',
        '- Stay inside the current working directory; the rules below are provided inline, do not try to read outside it.'
    ) -join "`n"
    $stdinText = "$shared`n`nPROJECT RULES CONTEXT:`n" + ($ctx -join "`n`n") + "`n`nTASK:`n$userPrompt`n"

    # Context is supplied inline from an explicit approved set.  Do not grant
    # broad Read/Glob/Grep: those permissions cannot reliably exclude secrets.
    $tools = @()
    $allow = @()
    if ($ReadWrite) {
        $tools += @('Edit', 'Write')
        foreach ($o in $own) { $allow += "Edit(./$o)"; $allow += "Write(./$o)" }
    }
    $settingsObj = @{
        permissions = @{ allow = @($allow); deny = @('Bash', 'WebFetch', 'WebSearch', 'Agent', 'Task') }
        env = @{
            DISABLE_TELEMETRY = '1'
            DISABLE_ERROR_REPORTING = '1'
            CLAUDE_CODE_SKIP_PROMPT_HISTORY = '1'
        }
    }
    $settingsJson = ConvertTo-Json -InputObject $settingsObj -Depth 6 -Compress

    $claude = Get-ClaudeExe
    $apiKey = Get-ApiKey
    # never transmit the key as task text; no silent redaction of the prompt
    if ($stdinText.Contains($apiKey) -or $settingsJson.Contains($apiKey)) {
        throw 'Assembled prompt/settings contain the API key; refusing to run.'
    }

    $budget = $MaxBudgetUsd.ToString([Globalization.CultureInfo]::InvariantCulture)
    $argList = @(
        '-p', '--bare', '--restricted',
        '--permission-mode', 'dontAsk',
        '--no-session-persistence',
        '--strict-mcp-config', '--mcp-config', '{"mcpServers":{}}',
        '--setting-sources', '',
        '--settings', $settingsJson,
        '--tools', ($tools -join ','),
        '--model', $Model,
        '--max-budget-usd', $budget,
        '--max-turns', [string]$MaxTurns,
        '--output-format', 'json'
    )
    $argString = ($argList | ForEach-Object { ConvertTo-WinArg ([string]$_) }) -join ' '

    $psi = New-Object System.Diagnostics.ProcessStartInfo
    $psi.FileName = $claude
    $psi.Arguments = $argString
    $psi.WorkingDirectory = $root
    $psi.UseShellExecute = $false
    $psi.CreateNoWindow = $true
    $psi.RedirectStandardInput = $true
    $psi.RedirectStandardOutput = $true
    $psi.RedirectStandardError = $true
    $psi.StandardOutputEncoding = [Text.Encoding]::UTF8
    $psi.StandardErrorEncoding = [Text.Encoding]::UTF8

    # child-only environment
    $ev = $psi.EnvironmentVariables
    foreach ($n in @($ev.Keys)) { $ev.Remove($n) }
    foreach ($n in @('SystemRoot','PATH','USERPROFILE','APPDATA','LOCALAPPDATA','TEMP','TMP','COMPUTERNAME')) {
        $v = [Environment]::GetEnvironmentVariable($n, 'Process')
        if (-not [string]::IsNullOrWhiteSpace($v)) { $ev[$n] = $v }
    }
    $ev['ANTHROPIC_BASE_URL'] = 'https://api.anthropic.com'
    $ev['ANTHROPIC_API_KEY'] = $apiKey
    $ev['CLAUDE_CODE_SKIP_PROMPT_HISTORY'] = '1'
    $ev['DISABLE_TELEMETRY'] = '1'
    $ev['DISABLE_ERROR_REPORTING'] = '1'

    $proc = New-Object System.Diagnostics.Process
    $proc.StartInfo = $psi
    [void]$proc.Start()
    $sw = [Diagnostics.Stopwatch]::StartNew()
    $timeoutMs = $TimeoutSeconds * 1000

    # asynchronous drains first (no deadlock), then async stdin write
    $outTask = $proc.StandardOutput.ReadToEndAsync()
    $errTask = $proc.StandardError.ReadToEndAsync()
    $bytes = (New-Object Text.UTF8Encoding($false)).GetBytes($stdinText)
    $inTask = $proc.StandardInput.BaseStream.WriteAsync($bytes, 0, $bytes.Length)
    $timedOut = $false
    try {
        if ($inTask.Wait($timeoutMs)) {
            $proc.StandardInput.BaseStream.Flush()
        } else { $timedOut = $true }
    } catch { }   # child may exit early; the exit code decides
    try { $proc.StandardInput.Close() } catch { }

    if (-not $timedOut) {
        $left = [Math]::Max(1, $timeoutMs - [int]$sw.ElapsedMilliseconds)
        if (-not $proc.WaitForExit($left)) { $timedOut = $true }
    }
    if ($timedOut) {
        # kill only our own child tree
        $kpsi = New-Object System.Diagnostics.ProcessStartInfo
        $kpsi.FileName = 'taskkill.exe'
        $kpsi.Arguments = '/PID ' + $proc.Id + ' /T /F'
        $kpsi.UseShellExecute = $false
        $kpsi.CreateNoWindow = $true
        $kpsi.RedirectStandardOutput = $true
        $kpsi.RedirectStandardError = $true
        $kp = [Diagnostics.Process]::Start($kpsi)
        [void]$kp.StandardOutput.ReadToEndAsync()
        [void]$kp.StandardError.ReadToEndAsync()
        if (-not $kp -or -not $kp.WaitForExit(15000) -or $kp.ExitCode -ne 0) {
            throw "Timed out after $TimeoutSeconds s; taskkill did not confirm success."
        }
        if (-not $proc.WaitForExit(15000)) {
            throw "Timed out after $TimeoutSeconds s; child process did not exit after taskkill."
        }
        throw "Timed out after $TimeoutSeconds s; child process tree kill confirmed."
    }

    [void]$outTask.Wait(15000)
    [void]$errTask.Wait(15000)
    $rawOut = ''; $rawErr = ''
    if ($outTask.IsCompleted) { $rawOut = $outTask.Result }
    if ($errTask.IsCompleted) { $rawErr = $errTask.Result }
    $code = $proc.ExitCode

    # redact the exact key BEFORE any use/printing
    $mask = '[REDACTED]'
    $safeOut = $rawOut.Replace($apiKey, $mask)
    $safeErr = $rawErr.Replace($apiKey, $mask)
    $rawOut = $null; $rawErr = $null

    if ($code -ne 0) {
        $msg = "claude exited with code $code."
        if ($safeErr.Trim().Length -gt 0) { $msg += ' stderr: ' + (Limit $safeErr.Trim() 300) }
        throw $msg
    }

    try { $j = $safeOut | ConvertFrom-Json } catch { throw 'Could not parse claude JSON output.' }
    $isErr = $false
    if ($j.PSObject.Properties['is_error']) { $isErr = [bool]$j.is_error }
    if ($isErr) {
        $why = ''
        if ($j.PSObject.Properties['result']) { $why = Limit ([string]$j.result) 300 }
        throw "claude reported is_error. $why"
    }
    $result = ''
    if ($j.PSObject.Properties['result']) { $result = ([string]$j.result).Replace($apiKey, $mask) }

    $modelUsed = $Model
    if ($j.PSObject.Properties['modelUsage'] -and $j.modelUsage) {
        $names = @($j.modelUsage.PSObject.Properties | ForEach-Object { $_.Name })
        if ($names.Count -gt 0) { $modelUsed = $names -join ',' }
    }
    $cost = 'n/a'; $turns = 'n/a'
    if ($j.PSObject.Properties['total_cost_usd']) { $cost = [string]$j.total_cost_usd }
    elseif ($j.PSObject.Properties['cost_usd']) { $cost = [string]$j.cost_usd }
    if ($j.PSObject.Properties['num_turns']) { $turns = [string]$j.num_turns }

    Write-Host $result
    Write-Host ''
    Write-Host ("[meta] model={0} cost_usd={1} turns={2}" -f $modelUsed, $cost, $turns)
    $exitCode = 0
}
catch {
    $m = $_.Exception.Message
    if ($apiKey) { $m = $m.Replace($apiKey, '[REDACTED]') }
    [Console]::Error.WriteLine("Invoke-ClaudeApiAgent failed: $m")
    $exitCode = 1
}
finally {
    # Best-effort cancellation cleanup for our own child; never touch unrelated PIDs.
    if ($proc) { try {
        if (-not $proc.HasExited) {
            $cleanup = Start-Process taskkill.exe -ArgumentList @('/PID', [string]$proc.Id, '/T', '/F') -PassThru -WindowStyle Hidden -Wait -ErrorAction Stop
            [void]$proc.WaitForExit(5000)
        }
    } catch { } }
    foreach ($h in @($kp, $proc)) {
        if ($h) { try { if ($h.HasExited) { $h.Dispose() } } catch { } }
    }
    $apiKey = $null
}
exit $exitCode
