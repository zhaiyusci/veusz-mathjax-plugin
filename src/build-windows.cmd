@echo off
rem ---------------------------------------------------------------------
rem Build mathjax_bridge.cpp into data\mathjaxbridge.dll (Windows, MSVC).
rem
rem This is step 2 of 2.  Step 1 (src\build-quickjs-windows.cmd) produces
rem data\qjs.dll; this script links against qjs's IMPORT library, so the two
rem binaries stay separate:
rem
rem   data\qjs.dll             upstream quickjs-ng, MIT, unmodified
rem   data\mathjaxbridge.dll   this JS host (Apache-2.0), imports qjs.dll
rem
rem USING_QJS_SHARED is what matters here: quickjs.h uses it to declare the
rem API with __declspec(dllimport).  Upstream's own CMake build adds it
rem automatically (as a PUBLIC definition on the qjs target); a plain cl call
rem has to pass it by hand.
rem
rem Needs:
rem   * MSVC (vcvars64.bat) - found automatically unless VCVARS is set
rem   * the QuickJS source tree for the headers      -> QUICKJS_SRC
rem   * the QuickJS IMPORT library (qjs.lib)         -> QUICKJS_LIB
rem
rem The QuickJS checkout may sit either inside this project or beside it, in
rem that order; the import library is taken from the matching build directory:
rem   %PROJECT%\quickjs-src + %PROJECT%\quickjs-build-shared
rem   %PROJECT%\..\quickjs-src + %PROJECT%\..\quickjs-build-shared
rem (run src\build-quickjs-windows.cmd first: it creates both)
rem
rem Usage:
rem   src\build-quickjs-windows.cmd      (once)
rem   src\build-windows.cmd
rem   set QUICKJS_SRC=C:\src\quickjs-ng
rem   set QUICKJS_LIB=C:\build-shared\qjs.lib
rem ---------------------------------------------------------------------
setlocal
set "HERE=%~dp0"
set "SRCDIR=%HERE:~0,-1%"
set "PROJECT=%SRCDIR%\.."
set "OUTDIR=%PROJECT%\data"
set "SRC=%SRCDIR%\mathjax_bridge.cpp"

rem ---- locate the checkout (project-local first, then beside the project) ----
set "QJS_LOCAL=%PROJECT%\quickjs-src"
if not "%QUICKJS_SRC%"=="" goto :have_src
if exist "%QJS_LOCAL%\quickjs.h" set "QUICKJS_SRC=%QJS_LOCAL%"
if not "%QUICKJS_SRC%"=="" goto :have_src
if exist "%PROJECT%\..\quickjs-src\quickjs.h" set "QUICKJS_SRC=%PROJECT%\..\quickjs-src"
:have_src

if not "%QUICKJS_LIB%"=="" goto :have_lib
if /i "%QUICKJS_SRC%"=="%QJS_LOCAL%" (
    set "QUICKJS_LIB=%PROJECT%\quickjs-build-shared\qjs.lib"
) else (
    set "QUICKJS_LIB=%PROJECT%\..\quickjs-build-shared\qjs.lib"
)
:have_lib

if "%VCVARS%"=="" (
    for %%V in (
        "C:\Program Files\Microsoft Visual Studio\18\Community\VC\Auxiliary\Build\vcvars64.bat"
        "C:\Program Files\Microsoft Visual Studio\18\Professional\VC\Auxiliary\Build\vcvars64.bat"
        "C:\Program Files\Microsoft Visual Studio\18\Enterprise\VC\Auxiliary\Build\vcvars64.bat"
        "C:\Program Files\Microsoft Visual Studio\2022\Community\VC\Auxiliary\Build\vcvars64.bat"
        "C:\Program Files\Microsoft Visual Studio\2022\Professional\VC\Auxiliary\Build\vcvars64.bat"
        "C:\Program Files\Microsoft Visual Studio\2022\Enterprise\VC\Auxiliary\Build\vcvars64.bat"
    ) do (
        if exist %%V set "VCVARS=%%~V"
    )
)

if not exist "%VCVARS%" ( echo [build] vcvars64.bat not found; set VCVARS or build with cmake & exit /b 1 )
if not exist "%SRC%"      ( echo [build] %SRC% not found & exit /b 1 )
if not exist "%QUICKJS_SRC%\quickjs.h" ( echo [build] QuickJS headers not found: %QUICKJS_SRC%\quickjs.h & exit /b 1 )
if not exist "%QUICKJS_LIB%" (
    echo [build] QuickJS import library not found: %QUICKJS_LIB%
    echo [build] run src\build-quickjs-windows.cmd first ^(it builds the
    echo [build] shared qjs.dll and its import library^), or set QUICKJS_LIB
    exit /b 1
)

call "%VCVARS%" >nul 2>&1
if not exist "%OUTDIR%" mkdir "%OUTDIR%"

cd /d "%SRCDIR%"
echo [build] compiling mathjaxbridge.dll (imports qjs.dll)
rem /MD matches how veusz and PyQt6 are built; /GL + /LTCG + /Gy match a
rem CMake Release build.  Nothing CRT-owned crosses the DLL boundary: every
rem buffer is allocated and freed inside the DLL.
rem (paths passed as arguments must not end in a backslash: it would escape
rem the closing quote for cl, which then sees no source file at all)
cl /nologo /LD /MD /O2 /GL /Gy /W3 /utf-8 /DNDEBUG /D_CRT_SECURE_NO_WARNINGS ^
   /DUSING_QJS_SHARED ^
   /I "%QUICKJS_SRC%" /I "%SRCDIR%" ^
   /Fe:"%OUTDIR%\mathjaxbridge.new.dll" ^
   "%SRC%" "%QUICKJS_LIB%" kernel32.lib user32.lib /link /LTCG /OPT:REF /OPT:ICF
if errorlevel 1 ( echo [build] FAILED & exit /b 1 )

del /q mathjax_bridge.obj mathjaxbridge.exp mathjaxbridge.lib 2>nul
del /q "%OUTDIR%\mathjaxbridge.new.exp" "%OUTDIR%\mathjaxbridge.new.lib" 2>nul

rem A running veusz keeps the DLL loaded, so build to a scratch name first and
rem only swap it in when that succeeds.
if exist "%OUTDIR%\mathjaxbridge.dll" copy /y "%OUTDIR%\mathjaxbridge.dll" "%OUTDIR%\mathjaxbridge.old.dll" >nul 2>&1
copy /y "%OUTDIR%\mathjaxbridge.new.dll" "%OUTDIR%\mathjaxbridge.dll" >nul 2>&1
if errorlevel 1 (
    echo [build] built %OUTDIR%\mathjaxbridge.new.dll
    echo [build] but %OUTDIR%\mathjaxbridge.dll is in use ^(a running veusz has
    echo [build] loaded it^), so it was not replaced.  Close veusz and re-run.
    exit /b 2
)
del /q "%OUTDIR%\mathjaxbridge.old.dll" "%OUTDIR%\mathjaxbridge.new.dll" 2>nul
echo [build] OK: %OUTDIR%\mathjaxbridge.dll
