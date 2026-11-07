' PanTransfer desktop launcher.
'
' Why a .vbs: if the packaged PanTransfer.exe starts the browser itself, the page
' does not render (blank window, title stuck at 127.0.0.1). Starting the browser
' from wscript/explorer works. So the shortcut points here:
'   1) start PanTransfer.exe --serve (server only, no window)
'   2) wait until /api/health answers
'   3) open the app window in Edge/Chrome, or the default browser
'
' Keep this file ASCII-only: cscript/wscript reads it as ANSI.

Option Explicit

Dim sh, fso, base, exe, portFile, port, url, i, browser, profile, args, ok

Set sh = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")

base = fso.GetParentFolderName(WScript.ScriptFullName)
exe = fso.BuildPath(base, "PanTransfer.exe")
If Not fso.FileExists(exe) Then
    MsgBox "PanTransfer.exe not found next to start_app.vbs.", 16, "PanTransfer"
    WScript.Quit 1
End If

portFile = fso.BuildPath(fso.BuildPath(base, "data"), "current_port")

' 1) server only, hidden
' Note: launching the exe directly with Run(...,0,...) fails with
' 0x800704C7 (ERROR_CANCELLED) here; going through "cmd /c start" works.
sh.CurrentDirectory = base
sh.Run "cmd /c start """" """ & exe & """ --serve", 0, False

' 2) wait for the port file, then for health
port = 0
For i = 1 To 60
    WScript.Sleep 500
    If fso.FileExists(portFile) Then
        On Error Resume Next
        port = CInt(Trim(fso.OpenTextFile(portFile, 1).ReadAll))
        On Error GoTo 0
        If port > 0 Then Exit For
    End If
Next

If port = 0 Then port = 8767
url = "http://127.0.0.1:" & port & "/"

ok = False
For i = 1 To 40
    If HttpOk(url & "api/health") Then
        ok = True
        Exit For
    End If
    WScript.Sleep 500
Next

If Not ok Then
    MsgBox "Server did not start. See data\launcher.log", 16, "PanTransfer"
    WScript.Quit 1
End If

' 3) app window
browser = FindBrowser()
If browser <> "" Then
    profile = fso.BuildPath(fso.BuildPath(base, "data"), "ui-profile")
    args = "--app=" & url & " --window-size=1280,880 --user-data-dir=""" & profile & _
           """ --no-first-run --no-default-browser-check"
    sh.Run "cmd /c start """" """ & browser & """ " & args, 0, False
Else
    sh.Run "cmd /c start """" "" & url", 0, False
End If

WScript.Quit 0


Function HttpOk(u)
    Dim req
    HttpOk = False
    On Error Resume Next
    Set req = CreateObject("MSXML2.XMLHTTP")
    req.Open "GET", u, False
    req.Send
    If Err.Number = 0 Then
        If req.Status = 200 Then HttpOk = True
    End If
    Err.Clear
    On Error GoTo 0
End Function


Function FindBrowser()
    Dim c, list
    list = Array( _
        sh.ExpandEnvironmentStrings("%ProgramFiles(x86)%") & "\Microsoft\Edge\Application\msedge.exe", _
        sh.ExpandEnvironmentStrings("%ProgramFiles%") & "\Microsoft\Edge\Application\msedge.exe", _
        sh.ExpandEnvironmentStrings("%LOCALAPPDATA%") & "\Microsoft\Edge\Application\msedge.exe", _
        sh.ExpandEnvironmentStrings("%ProgramFiles%") & "\Google\Chrome\Application\chrome.exe", _
        sh.ExpandEnvironmentStrings("%ProgramFiles(x86)%") & "\Google\Chrome\Application\chrome.exe")
    FindBrowser = ""
    For Each c In list
        If fso.FileExists(c) Then
            FindBrowser = c
            Exit Function
        End If
    Next
End Function
