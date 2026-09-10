@echo off
rem ---------------------------------------------------------------------
rem Build qjs.dll (shared QuickJS) from an UNMODIFIED quickjs-ng checkout.
rem
rem This is step 1 of 2.  The shipped layout is one licence per artefact:
rem
rem   data\qjs.dll             upstream quickjs-ng, MIT, no source change
rem   data\mathjaxbridge.dll   the JS host (Apache-2.0); links qjs's import lib
rem   data\mathjax_bundle.js   MathJax 4, Apache-2.0 (built separately)
rem
rem Upstream does all the work: -DBUILD_SHARED_LIBS=ON makes CMake set
rem QJS_LIB_TYPE=SHARED_LIBRARY and define BUILDING_QJS_SHARED, which is what
rem makes quickjs.h itself add __declspec(dllexport) to the API.
rem
rem The output keeps the upstream name qjs.dll on purpose: the import library
rem records that file name, so renaming the DLL after the build would leave
rem every consumer looking for a file that is no longer there.
rem
rem Needs:
rem   * MSVC (vcvars64.bat)   - found automatically unless VCVARS is set
rem   * cmake                 - found on PATH, or in the Qt / VS installs
rem   * a quickjs-ng checkout -> QUICKJS_SRC
rem
rem Usage:
rem   src\build-quickjs-windows.cmd
rem   set QUICKJS_SRC=C:\src\quickjs-ng
rem ---------------------------------------------------------------------
setlocal
set "HERE=%~dp0"
set "SRCDIR=%HERE:~0,-1%"
set "PROJECT=%SRCDIR%\.."
set "OUTDIR=%PROJECT%\data"

if "%QUICKJS_SRC%"=="" set "QUICKJS_SRC=%PROJECT%\..\quickjs-src"
if "%QUICKJS_BUILD%"=="" set "QUICKJS_BUILD=%PROJECT%\..\quickjs-build-shared"

rem ---- find vcvars64 -----------------------------------------------------
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

rem ---- find cmake --------------------------------------------------------
if "%CMAKE%"=="" (
    for /f "delims=" %%C in ('where cmake 2^>nul') do (
        if not defined CMAKE set "CMAKE=%%C"
    )
)
if "%CMAKE%"=="" (
    for %%C in (
        "C:\Qt\Tools\CMake_64\bin\cmake.exe"
        "C:\Qt\Tools\CMake\bin\cmake.exe"
        "C:\Program Files\CMake\bin\cmake.exe"
        "C:\Program Files\Microsoft Visual Studio\18\Community\Common7\IDE\CommonExtensions\Microsoft\CMake\CMake\bin\cmake.exe"
        "C:\Program Files\Microsoft Visual Studio\2022\Community\Common7\IDE\CommonExtensions\Microsoft\CMake\CMake\bin\cmake.exe"
    ) do (
        if not defined CMAKE if exist %%C set "CMAKE=%%~C"
    )
)

if not exist "%VCVARS%" ( echo [quickjs] vcvars64.bat not found; set VCVARS & exit /b 1 )
if "%CMAKE%"=="" ( echo [quickjs] cmake not found; set CMAKE & exit /b 1 )
if not exist "%QUICKJS_SRC%\quickjs.h" ( echo [quickjs] headers not found: %QUICKJS_SRC%\quickjs.h & echo [quickjs] set QUICKJS_SRC, or clone quickjs-ng & exit /b 1 )

call "%VCVARS%" >nul 2>&1
if not exist "%QUICKJS_BUILD%" mkdir "%QUICKJS_BUILD%"
if not exist "%OUTDIR%" mkdir "%OUTDIR%"

echo [quickjs] configuring (shared library, release)
"%CMAKE%" -S "%QUICKJS_SRC%" -B "%QUICKJS_BUILD%" -G "NMake Makefiles" ^
    -DCMAKE_BUILD_TYPE=Release ^
    -DBUILD_SHARED_LIBS=ON ^
    -DQJS_BUILD_EXAMPLES=OFF
if errorlevel 1 ( echo [quickjs] configure FAILED & exit /b 1 )

echo [quickjs] building qjs.dll
"%CMAKE%" --build "%QUICKJS_BUILD%"
if errorlevel 1 ( echo [quickjs] build FAILED & exit /b 1 )

set "QJSDLL="
for /r "%QUICKJS_BUILD%" %%f in (qjs.dll) do if not defined QJSDLL set "QJSDLL=%%f"
if not defined QJSDLL ( echo [quickjs] qjs.dll not found under %QUICKJS_BUILD% & exit /b 1 )

rem Build to a scratch name first: a running veusz may have the old one loaded.
copy /y "%QJSDLL%" "%OUTDIR%\qjs.new.dll" >nul
if errorlevel 1 ( echo [quickjs] cannot write %OUTDIR%\qjs.new.dll & exit /b 1 )

if exist "%OUTDIR%\qjs.dll" copy /y "%OUTDIR%\qjs.dll" "%OUTDIR%\qjs.old.dll" >nul 2>&1
copy /y "%OUTDIR%\qjs.new.dll" "%OUTDIR%\qjs.dll" >nul 2>&1
if errorlevel 1 (
    echo [quickjs] built %OUTDIR%\qjs.new.dll
    echo [quickjs] but %OUTDIR%\qjs.dll is in use ^(a running veusz loaded it^),
    echo [quickjs] so it was not replaced.  Close veusz and re-run.
    exit /b 2
)
del /q "%OUTDIR%\qjs.old.dll" "%OUTDIR%\qjs.new.dll" 2>nul
echo [quickjs] OK: %OUTDIR%\qjs.dll
