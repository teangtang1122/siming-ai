param(
  [ValidateSet("auto", "readonly_collaboration", "draft_generation", "project_writing", "project_management", "internal_llm", "trusted_local_maintenance")]
  [string]$PermissionPack = "auto",
  [string]$ProjectId = "",
  [string]$MoshuExe = "",
  [string]$SourceRoot = "",
  [ValidateSet("auto", "claude", "codex", "all")]
  [string]$Client = "auto",
  [ValidateSet("local", "user", "project")]
  [string]$ClaudeScope = "user",
  [switch]$PreferSource,
  [switch]$DryRun
)

$ErrorActionPreference = "Stop"

function Write-Step {
  param([string]$Message)
  Write-Host "[Moshu MCP] $Message"
}

function Get-CommandPath {
  param([string[]]$Names)
  foreach ($name in $Names) {
    $cmd = Get-Command $name -ErrorAction SilentlyContinue
    if ($cmd) {
      return $cmd.Source
    }
  }
  return $null
}

function Escape-TomlString {
  param([string]$Value)
  return '"' + (($Value -replace '\\', '\\') -replace '"', '\"') + '"'
}

function Format-TomlArray {
  param([string[]]$Values)
  $escaped = @()
  foreach ($value in $Values) {
    $escaped += (Escape-TomlString $value)
  }
  return "[" + ($escaped -join ", ") + "]"
}

function Add-OptionalProjectId {
  param([string[]]$BaseArgs)
  $result = @($BaseArgs)
  if ($ProjectId.Trim()) {
    $result += @("--project-id", $ProjectId.Trim())
  }
  return $result
}

function Test-MoshuExe {
  param([string]$Path)
  return ($Path -and (Test-Path -LiteralPath $Path) -and ((Split-Path -Leaf $Path) -match '^(Moshu|NovelWritingAgent)\.exe$'))
}

function Find-NearbyExe {
  $scriptDir = Split-Path -Parent $MyInvocation.ScriptName
  $repoRoot = Split-Path -Parent $scriptDir
  $candidates = @()

  if ($MoshuExe) {
    $candidates += $MoshuExe
  }
  if ($env:MOSHU_EXE) {
    $candidates += $env:MOSHU_EXE
  }
  $candidates += @(
    (Join-Path $repoRoot "release\Moshu.exe"),
    (Join-Path $repoRoot "release\NovelWritingAgent.exe"),
    (Join-Path $scriptDir "Moshu.exe"),
    (Join-Path $scriptDir "NovelWritingAgent.exe"),
    (Join-Path (Get-Location) "Moshu.exe"),
    (Join-Path (Get-Location) "NovelWritingAgent.exe")
  )

  $commonDirs = @()
  if ($env:USERPROFILE) {
    $commonDirs += @(
      (Join-Path $env:USERPROFILE "Downloads"),
      (Join-Path $env:USERPROFILE "Desktop")
    )
  }
  if ($env:LOCALAPPDATA) {
    $commonDirs += @(
      (Join-Path $env:LOCALAPPDATA "Moshu"),
      (Join-Path $env:LOCALAPPDATA "NovelWritingAgent")
    )
  }

  foreach ($dir in $commonDirs) {
    if (-not (Test-Path -LiteralPath $dir)) {
      continue
    }
    $candidates += (Join-Path $dir "Moshu.exe")
    $candidates += (Join-Path $dir "NovelWritingAgent.exe")
    try {
      $children = Get-ChildItem -LiteralPath $dir -Directory -ErrorAction SilentlyContinue | Select-Object -First 40
      foreach ($child in $children) {
        $candidates += (Join-Path $child.FullName "Moshu.exe")
        $candidates += (Join-Path $child.FullName "NovelWritingAgent.exe")
      }
    } catch {
      # Ignore folders we cannot inspect.
    }
  }

  $found = @()
  foreach ($candidate in $candidates) {
    if (Test-MoshuExe $candidate) {
      $found += (Get-Item -LiteralPath $candidate)
    }
  }
  if ($found.Count -eq 0) {
    return $null
  }
  return (
    $found |
      Sort-Object `
        @{ Expression = { if ($_.Name -eq "Moshu.exe") { 0 } else { 1 } } }, `
        @{ Expression = { $_.LastWriteTime }; Descending = $true } |
      Select-Object -First 1
  ).FullName
}

function Find-SourceEntrypoint {
  $scriptDir = Split-Path -Parent $MyInvocation.ScriptName
  $repoRoot = Split-Path -Parent $scriptDir
  $roots = @()
  if ($SourceRoot) {
    $roots += $SourceRoot
  }
  $roots += $repoRoot
  $roots += (Get-Location).Path

  foreach ($root in $roots) {
    if (-not $root) {
      continue
    }
    $entry = Join-Path $root "scripts\moshu-mcp-server.py"
    if (Test-Path -LiteralPath $entry) {
      return @{
        Root = (Resolve-Path -LiteralPath $root).Path
        Entry = (Resolve-Path -LiteralPath $entry).Path
      }
    }
  }
  return $null
}

function Resolve-McpCommand {
  if (-not $PreferSource) {
    $exe = Find-NearbyExe
    if ($exe) {
      $args = Add-OptionalProjectId -BaseArgs @("--mcp-server", "--permission-pack", $PermissionPack)
      return @{
        Mode = "exe"
        Command = $exe
        Args = $args
        Cwd = ""
      }
    }
  }

  $source = Find-SourceEntrypoint
  if ($source) {
    $python = Get-CommandPath @("python", "py")
    if (-not $python) {
      if ($PreferSource) {
        throw "Could not find python/py for source MCP entrypoint. Install Python or pass -MoshuExe."
      }
    } else {
      $args = Add-OptionalProjectId -BaseArgs @($source.Entry, "--permission-pack", $PermissionPack)
      return @{
        Mode = "source"
        Command = $python
        Args = $args
        Cwd = $source.Root
      }
    }
  }

  $exeFallback = Find-NearbyExe
  if ($exeFallback) {
    $args = Add-OptionalProjectId -BaseArgs @("--mcp-server", "--permission-pack", $PermissionPack)
    return @{
      Mode = "exe"
      Command = $exeFallback
      Args = $args
      Cwd = ""
    }
  }

  throw "Could not find Moshu.exe or scripts\moshu-mcp-server.py. Pass -MoshuExe or -SourceRoot."
}

function Configure-ClaudeCode {
  param([hashtable]$Server)

  $claude = Get-CommandPath @("claude", "claude.cmd", "claude.exe")
  if (-not $claude) {
    Write-Step "Claude Code not found. Skipping Claude Code configuration."
    return $false
  }

  $addArgs = @("mcp", "add", "-s", $ClaudeScope, "moshu", "--", $Server.Command) + $Server.Args
  Write-Step "Claude Code detected: $claude"
  Write-Step "Claude command: claude $($addArgs -join ' ')"
  if ($DryRun) {
    return $true
  }

  try {
    & $claude "mcp" "remove" "-s" $ClaudeScope "moshu" *> $null
  } catch {
    # It is fine if the server did not exist yet.
  }
  & $claude @addArgs
  if ($LASTEXITCODE -ne 0) {
    throw "Claude Code MCP configuration failed."
  }
  return $true
}

function Configure-Codex {
  param([hashtable]$Server)

  $codexCommand = Get-CommandPath @("codex", "codex.exe")
  $codexHome = Join-Path $env:USERPROFILE ".codex"
  $configPath = Join-Path $codexHome "config.toml"
  $codexLooksInstalled = ($codexCommand -or (Test-Path -LiteralPath $codexHome))
  if (-not $codexLooksInstalled) {
    Write-Step "Codex not found. Skipping Codex configuration."
    return $false
  }

  $block = @(
    "[mcp_servers.moshu]",
    'type = "stdio"',
    "command = $(Escape-TomlString $Server.Command)",
    "args = $(Format-TomlArray $Server.Args)"
  ) -join [Environment]::NewLine

  Write-Step "Codex config: $configPath"
  Write-Step "Codex MCP block:"
  Write-Host $block
  if ($DryRun) {
    return $true
  }

  if (-not (Test-Path -LiteralPath $codexHome)) {
    New-Item -ItemType Directory -Path $codexHome | Out-Null
  }
  $old = ""
  if (Test-Path -LiteralPath $configPath) {
    $old = Get-Content -LiteralPath $configPath -Raw
    $backup = "$configPath.bak-$(Get-Date -Format 'yyyyMMdd-HHmmss')"
    Copy-Item -LiteralPath $configPath -Destination $backup -Force
    Write-Step "Backed up existing Codex config to $backup"
  }

  $pattern = '(?ms)^\[mcp_servers\.moshu\]\r?\n.*?(?=^\[|\z)'
  if ($old -match $pattern) {
    $new = [regex]::Replace($old, $pattern, ($block + [Environment]::NewLine))
  } else {
    $trimmed = $old.TrimEnd()
    if ($trimmed) {
      $new = $trimmed + [Environment]::NewLine + [Environment]::NewLine + $block + [Environment]::NewLine
    } else {
      $new = $block + [Environment]::NewLine
    }
  }
  Set-Content -LiteralPath $configPath -Encoding UTF8 -Value $new
  return $true
}

$server = Resolve-McpCommand
Write-Step "Selected MCP server mode: $($server.Mode)"
Write-Step "Command: $($server.Command)"
Write-Step "Args: $($server.Args -join ' ')"
if ($PermissionPack -ne "auto") {
  Write-Host "[Moshu MCP] WARNING: Using fixed permission pack '$PermissionPack'. This bypasses UI global permission changes." -ForegroundColor Yellow
}
if ($server.Cwd) {
  Write-Step "Source root: $($server.Cwd)"
}

$configureClaude = $Client -in @("auto", "claude", "all")
$configureCodex = $Client -in @("auto", "codex", "all")
$configuredAny = $false

if ($configureClaude) {
  $configuredAny = (Configure-ClaudeCode $server) -or $configuredAny
}
if ($configureCodex) {
  $configuredAny = (Configure-Codex $server) -or $configuredAny
}

if (-not $configuredAny) {
  Write-Step "No supported MCP clients were found. Install Claude Code or Codex, then run this script again."
  exit 1
}

if ($DryRun) {
  Write-Step "Dry run complete. No files were modified."
} else {
  Write-Step "Configuration complete. Restart Claude Code/Codex, then ask it to call list_projects."
}
