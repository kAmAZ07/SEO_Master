param(
    [Parameter(Mandatory = $true)]
    [ValidateSet('restrict', 'check')]
    [string]$Action,
    [Parameter(Mandatory = $true)]
    [string]$Path
)

$ErrorActionPreference = 'Stop'
function Invoke-PrivateIcacls {
    param([string[]]$Arguments)
    & "$env:SYSTEMROOT\System32\icacls.exe" @Arguments | Out-Null
    if ($LASTEXITCODE -ne 0) {
        throw 'ACL_POLICY_REJECTED'
    }
}
try {
    $Identity = [System.Security.Principal.WindowsIdentity]::GetCurrent().User
    $IsDirectory = (Get-Item -LiteralPath $Path -Force).PSIsContainer
    if ($Action -eq 'restrict') {
        $Existing = (Get-Acl -LiteralPath $Path).GetAccessRules(
            $true, $false, [System.Security.Principal.SecurityIdentifier]
        )
        $ForeignSids = @($Existing | Where-Object {
            $_.IdentityReference.Value -ne $Identity.Value
        } | ForEach-Object { "*$($_.IdentityReference.Value)" } | Select-Object -Unique)
        $OwnerDenies = @($Existing | Where-Object {
            $_.IdentityReference.Value -eq $Identity.Value -and
            $_.AccessControlType -eq [System.Security.AccessControl.AccessControlType]::Deny
        })
        if ($IsDirectory) {
            $Grant = "*$($Identity.Value):(OI)(CI)F"
        }
        else {
            $Grant = "*$($Identity.Value):F"
        }
        Invoke-PrivateIcacls -Arguments @($Path, '/setowner', "*$($Identity.Value)")
        Invoke-PrivateIcacls -Arguments @($Path, '/inheritance:r', '/grant:r', $Grant)
        foreach ($Sid in $ForeignSids) {
            Invoke-PrivateIcacls -Arguments @($Path, '/remove', $Sid)
        }
        if ($OwnerDenies.Count -gt 0) {
            Invoke-PrivateIcacls -Arguments @($Path, '/remove:d', "*$($Identity.Value)")
        }
    }
    $Security = Get-Acl -LiteralPath $Path
    $Owner = $Security.GetOwner([System.Security.Principal.SecurityIdentifier])
    $Rules = @($Security.GetAccessRules($true, $true, [System.Security.Principal.SecurityIdentifier]))
    if (-not $Security.AreAccessRulesProtected -or $Owner.Value -ne $Identity.Value -or $Rules.Count -ne 1) {
        throw 'ACL_POLICY_REJECTED'
    }
    $Rule = $Rules[0]
    if ($Rule.IdentityReference.Value -ne $Identity.Value -or
        $Rule.AccessControlType -ne [System.Security.AccessControl.AccessControlType]::Allow -or
        $Rule.FileSystemRights -ne [System.Security.AccessControl.FileSystemRights]::FullControl -or
        $Rule.IsInherited) {
        throw 'ACL_POLICY_REJECTED'
    }
    exit 0
}
catch {
    [Console]::Error.WriteLine('ACL_POLICY_REJECTED')
    exit 2
}
