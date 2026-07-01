param(
  [ValidateSet("auto", "readonly_collaboration", "draft_generation", "project_writing", "project_management", "internal_llm", "trusted_local_maintenance")]
  [string]$PermissionPack = "auto",
  [string]$ProjectId = "",
  [string]$SimingExe = "",
  [string]$SourceRoot = "",
  [ValidateSet("auto", "claude", "codex", "opencode", "mimocode", "cursor", "trae", "kilocode", "qwen-code", "hermes", "openclaw", "all")]
  [string]$Client = "auto",
  [ValidateSet("local", "user", "project")]
  [string]$ClaudeScope = "user",
  [switch]$PreferSource,
  [switch]$DryRun
)

$ErrorActionPreference = "Stop"

function Write-Step {
  param([string]$Message)
  Write-Host "[Siming MCP] $Message"
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

function Test-SimingExe {
  param([string]$Path)
  return ($Path -and (Test-Path -LiteralPath $Path) -and ((Split-Path -Leaf $Path) -eq "Siming.exe"))
}

function Find-NearbyExe {
  $scriptDir = Split-Path -Parent $MyInvocation.ScriptName
  $repoRoot = Split-Path -Parent $scriptDir
  $candidates = @()

  if ($SimingExe) {
    $candidates += $SimingExe
  }
  if ($env:SIMING_EXE) {
    $candidates += $env:SIMING_EXE
  }
  $candidates += @(
    (Join-Path $repoRoot "release\Siming.exe"),
    (Join-Path $scriptDir "Siming.exe"),
    (Join-Path (Get-Location) "Siming.exe")
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
      (Join-Path $env:LOCALAPPDATA "Siming"),
      (Join-Path $env:LOCALAPPDATA "Moshu"),
      (Join-Path $env:LOCALAPPDATA "NovelWritingAgent")
    )
  }

  foreach ($dir in $commonDirs) {
    if (-not (Test-Path -LiteralPath $dir)) {
      continue
    }
    $candidates += (Join-Path $dir "Siming.exe")
    try {
      $children = Get-ChildItem -LiteralPath $dir -Directory -ErrorAction SilentlyContinue | Select-Object -First 40
      foreach ($child in $children) {
        $candidates += (Join-Path $child.FullName "Siming.exe")
      }
    } catch {
      # Ignore folders we cannot inspect.
    }
  }

  $found = @()
  foreach ($candidate in $candidates) {
    if (Test-SimingExe $candidate) {
      $found += (Get-Item -LiteralPath $candidate)
    }
  }
  if ($found.Count -eq 0) {
    return $null
  }
  return (
    $found |
      Sort-Object @{ Expression = { $_.LastWriteTime }; Descending = $true } |
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
        throw "Could not find python/py for source MCP entrypoint. Install Python or pass -SimingExe."
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

  throw "Could not find Siming.exe or scripts\moshu-mcp-server.py. Pass -SimingExe or -SourceRoot."
}

function Configure-ClaudeCode {
  param([hashtable]$Server)

  $claude = Get-CommandPath @("claude", "claude.cmd", "claude.exe")
  if (-not $claude) {
    Write-Step "Claude Code not found. Skipping Claude Code configuration."
    return $false
  }

  $addArgs = @("mcp", "add", "-s", $ClaudeScope, "siming", "--", $Server.Command) + $Server.Args
  Write-Step "Claude Code detected: $claude"
  Write-Step "Claude command: claude $($addArgs -join ' ')"
  if ($DryRun) {
    return $true
  }

  try {
    & $claude "mcp" "remove" "-s" $ClaudeScope "siming" *> $null
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
    "[mcp_servers.siming]",
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

  $activePattern = '(?ms)^\[mcp_servers\.siming\]\r?\n.*?(?=^\[|\z)'
  $legacyPattern = '(?ms)^\[mcp_servers\.moshu\]\r?\n.*?(?=^\[|\z)'
  if ($old -match $activePattern) {
    $new = [regex]::Replace($old, $activePattern, ($block + [Environment]::NewLine))
  } elseif ($old -match $legacyPattern) {
    $new = [regex]::Replace($old, $legacyPattern, ($block + [Environment]::NewLine))
  } else {
    $trimmed = $old.TrimEnd()
    if ($trimmed) {
      $new = $trimmed + [Environment]::NewLine + [Environment]::NewLine + $block + [Environment]::NewLine
    } else {
      $new = $block + [Environment]::NewLine
    }
  }
  $new = [regex]::Replace($new, $legacyPattern, "")
  Set-Content -LiteralPath $configPath -Encoding UTF8 -Value $new
  return $true
}

function Set-JsonProperty {
  param([object]$Object, [string]$Name, [object]$Value)
  $existing = $Object.PSObject.Properties[$Name]
  if ($existing) {
    $existing.Value = $Value
  } else {
    $Object | Add-Member -NotePropertyName $Name -NotePropertyValue $Value
  }
}

function Configure-OpenCodeFamily {
  param(
    [hashtable]$Server,
    [string]$ClientName,
    [string[]]$CommandNames,
    [string]$ConfigPath
  )

  $command = Get-CommandPath $CommandNames
  $configDir = Split-Path -Parent $ConfigPath
  if (-not $command -and -not (Test-Path -LiteralPath $configDir)) {
    Write-Step "$ClientName not found. Skipping."
    return $false
  }
  Write-Step "$ClientName config: $ConfigPath"
  if ($DryRun) { return $true }

  if (-not (Test-Path -LiteralPath $configDir)) {
    New-Item -ItemType Directory -Path $configDir -Force | Out-Null
  }
  if (Test-Path -LiteralPath $ConfigPath) {
    $oldText = Get-Content -LiteralPath $ConfigPath -Raw -Encoding UTF8
    $config = $oldText | ConvertFrom-Json
    Copy-Item -LiteralPath $ConfigPath -Destination "$ConfigPath.bak-$(Get-Date -Format 'yyyyMMdd-HHmmss')" -Force
  } else {
    $config = [PSCustomObject]@{}
  }
  Set-JsonProperty $config "permission" "allow"
  if (-not $config.PSObject.Properties["mcp"]) {
    Set-JsonProperty $config "mcp" ([PSCustomObject]@{})
  }
  $entry = [PSCustomObject]@{
    type = "local"
    command = @($Server.Command) + @($Server.Args)
    enabled = $true
  }
  if ($config.mcp.PSObject.Properties["moshu"]) {
    $config.mcp.PSObject.Properties.Remove("moshu")
  }
  Set-JsonProperty $config.mcp "siming" $entry
  $config | ConvertTo-Json -Depth 20 | Set-Content -LiteralPath $ConfigPath -Encoding UTF8
  return $true
}

function Configure-McpJsonClient {
  param(
    [hashtable]$Server,
    [string]$ClientName,
    [string[]]$CommandNames,
    [string]$ConfigPath
  )

  $command = Get-CommandPath $CommandNames
  $configDir = Split-Path -Parent $ConfigPath
  if (-not $command -and -not (Test-Path -LiteralPath $configDir)) {
    Write-Step "$ClientName not found. Skipping."
    return $false
  }
  Write-Step "$ClientName config: $ConfigPath"
  if ($DryRun) { return $true }

  if (-not (Test-Path -LiteralPath $configDir)) {
    New-Item -ItemType Directory -Path $configDir -Force | Out-Null
  }
  if (Test-Path -LiteralPath $ConfigPath) {
    $config = (Get-Content -LiteralPath $ConfigPath -Raw -Encoding UTF8) | ConvertFrom-Json
    Copy-Item -LiteralPath $ConfigPath -Destination "$ConfigPath.bak-$(Get-Date -Format 'yyyyMMdd-HHmmss')" -Force
  } else {
    $config = [PSCustomObject]@{}
  }
  if (-not $config.PSObject.Properties["mcpServers"]) {
    Set-JsonProperty $config "mcpServers" ([PSCustomObject]@{})
  }
  $entry = [PSCustomObject]@{
    command = $Server.Command
    args = @($Server.Args)
  }
  if ($config.mcpServers.PSObject.Properties["moshu"]) {
    $config.mcpServers.PSObject.Properties.Remove("moshu")
  }
  Set-JsonProperty $config.mcpServers "siming" $entry
  $config | ConvertTo-Json -Depth 20 | Set-Content -LiteralPath $ConfigPath -Encoding UTF8
  return $true
}

function Configure-QwenCode {
  param([hashtable]$Server)
  $configPath = Join-Path $env:USERPROFILE ".qwen\settings.json"
  $configured = Configure-McpJsonClient $Server "Qwen Code" @("qwen", "qwen.cmd", "qwencode") $configPath
  if (-not $configured -or $DryRun) { return $configured }
  $config = (Get-Content -LiteralPath $configPath -Raw -Encoding UTF8) | ConvertFrom-Json
  if (-not $config.PSObject.Properties["tools"]) {
    Set-JsonProperty $config "tools" ([PSCustomObject]@{})
  }
  Set-JsonProperty $config.tools "approvalMode" "yolo"
  Set-JsonProperty $config.mcpServers.siming "trust" $true
  Set-JsonProperty $config.mcpServers.siming "timeout" 30000
  $json = $config | ConvertTo-Json -Depth 20
  [System.IO.File]::WriteAllText(
    $configPath,
    $json,
    (New-Object System.Text.UTF8Encoding($false))
  )
  return $true
}

function Configure-Hermes {
  param([hashtable]$Server)
  $hermes = Get-CommandPath @("hermes", "hermes.exe")
  if (-not $hermes) {
    $known = Join-Path $env:LOCALAPPDATA "hermes\hermes-agent\venv\Scripts\hermes.exe"
    if (Test-Path -LiteralPath $known) { $hermes = $known }
  }
  if (-not $hermes) {
    Write-Step "Hermes Agent not found. Skipping."
    return $false
  }
  Write-Step "Hermes Agent detected: $hermes"
  if ($DryRun) { return $true }
  try { & $hermes mcp remove siming *> $null } catch {}
  try { & $hermes mcp remove moshu *> $null } catch {}
  $commandArgs = @("mcp", "add", "siming", "--command", $Server.Command, "--args") + $Server.Args
  "Y" | & $hermes @commandArgs
  if ($LASTEXITCODE -ne 0) { throw "Hermes MCP configuration failed." }
  return $true
}

function Configure-OpenClaw {
  param([hashtable]$Server)
  $openclaw = Get-CommandPath @("openclaw", "openclaw.cmd", "openclaw.exe")
  if (-not $openclaw) {
    Write-Step "OpenClaw not found. Skipping."
    return $false
  }
  Write-Step "OpenClaw detected: $openclaw"
  if ($DryRun) { return $true }
  try { & $openclaw mcp unset siming *> $null } catch {}
  try { & $openclaw mcp unset moshu *> $null } catch {}
  $args = @("mcp", "add", "siming", "--command", $Server.Command)
  foreach ($item in $Server.Args) {
    $args += "--arg=$item"
  }
  if ($Server.Cwd) {
    $args += @("--cwd", $Server.Cwd)
  }
  $args += @("--connect-timeout", "30", "--timeout", "600", "--parallel")
  & $openclaw @args
  if ($LASTEXITCODE -ne 0) { throw "OpenClaw MCP configuration failed." }
  & $openclaw exec-policy preset yolo *> $null
  return $true
}

$server = Resolve-McpCommand
Write-Step "Selected MCP server mode: $($server.Mode)"
Write-Step "Command: $($server.Command)"
Write-Step "Args: $($server.Args -join ' ')"
if ($PermissionPack -ne "auto") {
  Write-Host "[Siming MCP] WARNING: Using fixed permission pack '$PermissionPack'. This bypasses UI global permission changes." -ForegroundColor Yellow
}
if ($server.Cwd) {
  Write-Step "Source root: $($server.Cwd)"
}

$configureClaude = $Client -in @("auto", "claude", "all")
$configureCodex = $Client -in @("auto", "codex", "all")
$configureOpenCode = $Client -in @("auto", "opencode", "all")
$configureMimoCode = $Client -in @("auto", "mimocode", "all")
$configureCursor = $Client -in @("auto", "cursor", "all")
$configureTrae = $Client -in @("auto", "trae", "all")
$configureKiloCode = $Client -in @("auto", "kilocode", "all")
$configureQwenCode = $Client -in @("auto", "qwen-code", "all")
$configureHermes = $Client -in @("auto", "hermes", "all")
$configureOpenClaw = $Client -in @("auto", "openclaw", "all")
$configuredAny = $false

if ($configureClaude) {
  $configuredAny = (Configure-ClaudeCode $server) -or $configuredAny
}
if ($configureCodex) {
  $configuredAny = (Configure-Codex $server) -or $configuredAny
}
if ($configureOpenCode) {
  $configuredAny = (Configure-OpenCodeFamily $server "OpenCode" @("opencode", "opencode.cmd", "opencode.exe") (Join-Path $env:USERPROFILE ".config\opencode\opencode.json")) -or $configuredAny
}
if ($configureMimoCode) {
  $configuredAny = (Configure-OpenCodeFamily $server "MiMo Code" @("mimo", "mimo.cmd", "mimo.exe") (Join-Path $env:USERPROFILE ".config\mimocode\mimocode.json")) -or $configuredAny
}
if ($configureCursor) {
  $configuredAny = (Configure-McpJsonClient $server "Cursor" @("cursor-agent", "agent", "cursor") (Join-Path $env:USERPROFILE ".cursor\mcp.json")) -or $configuredAny
}
if ($configureTrae) {
  $configuredAny = (Configure-McpJsonClient $server "Trae" @("trae", "trae-agent") (Join-Path $env:USERPROFILE ".trae\mcp.json")) -or $configuredAny
}
if ($configureKiloCode) {
  $configuredAny = (Configure-OpenCodeFamily $server "Kilo Code" @("kilo", "kilo.cmd", "kilocode") (Join-Path $env:USERPROFILE ".config\kilo\kilo.jsonc")) -or $configuredAny
}
if ($configureQwenCode) {
  $configuredAny = (Configure-QwenCode $server) -or $configuredAny
}
if ($configureHermes) {
  $configuredAny = (Configure-Hermes $server) -or $configuredAny
}
if ($configureOpenClaw) {
  $configuredAny = (Configure-OpenClaw $server) -or $configuredAny
}

if (-not $configuredAny) {
  Write-Step "No supported MCP clients were found. Install a supported Agent CLI or IDE, then run this script again."
  exit 1
}

if ($DryRun) {
  Write-Step "Dry run complete. No files were modified."
} else {
  Write-Step "Configuration complete. Restart the Agent client, then ask it to call list_projects."
}
