Dim WshShell, oExec
Set WshShell = CreateObject("WScript.Shell")

Dim logFile
logFile = "C:\Users\adity\OneDrive\Desktop\Pikina\startup_debug.log"

Dim fso, f
Set fso = CreateObject("Scripting.FileSystemObject")
Set f = fso.CreateTextFile(logFile, True)

f.WriteLine "=== Pikina Startup Debug ===" 
f.WriteLine "Timestamp: " & Now()

' Test pythonw
Dim pythonw
pythonw = "C:\Program Files\Python310\pythonw.exe"
If fso.FileExists(pythonw) Then
    f.WriteLine "pythonw.exe FOUND: " & pythonw
Else
    f.WriteLine "ERROR: pythonw.exe NOT FOUND at: " & pythonw
End If

' Test electron
Dim electronExe
electronExe = "C:\Users\adity\OneDrive\Desktop\Pikina\frontend\node_modules\electron\dist\electron.exe"
If fso.FileExists(electronExe) Then
    f.WriteLine "electron.exe FOUND: " & electronExe
Else
    f.WriteLine "ERROR: electron.exe NOT FOUND at: " & electronExe
End If

' Test backend_server.py
Dim backendScript
backendScript = "C:\Users\adity\OneDrive\Desktop\Pikina\backend_server.py"
If fso.FileExists(backendScript) Then
    f.WriteLine "backend_server.py FOUND"
Else
    f.WriteLine "ERROR: backend_server.py NOT FOUND"
End If

' Try launching backend
Dim backendCmd
backendCmd = Chr(34) & pythonw & Chr(34) & " " & Chr(34) & backendScript & Chr(34)
f.WriteLine "Launching backend: " & backendCmd
On Error Resume Next
WshShell.Run backendCmd, 0, False
If Err.Number <> 0 Then
    f.WriteLine "ERROR launching backend: " & Err.Description
    Err.Clear
Else
    f.WriteLine "Backend launch command sent OK"
End If
On Error GoTo 0

WScript.Sleep 2000

' Try launching electron
Dim electronCmd
electronCmd = Chr(34) & electronExe & Chr(34) & " " & Chr(34) & "C:\Users\adity\OneDrive\Desktop\Pikina\frontend" & Chr(34)
f.WriteLine "Launching electron: " & electronCmd
On Error Resume Next
WshShell.Run electronCmd, 1, False
If Err.Number <> 0 Then
    f.WriteLine "ERROR launching electron: " & Err.Description
    Err.Clear
Else
    f.WriteLine "Electron launch command sent OK"
End If
On Error GoTo 0

f.WriteLine "Script complete."
f.Close

Set f = Nothing
Set fso = Nothing
Set WshShell = Nothing
