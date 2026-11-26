#define _WIN32_WINNT 0x0A00
#define WIN32_LEAN_AND_MEAN
#include <windows.h>
#include <wchar.h>
#include <string.h>

enum { MAX_PATH_W = 32768, SOURCE_REFUSED = 78 };

static wchar_t root[MAX_PATH_W], runtime[MAX_PATH_W], python[MAX_PATH_W], python_dll[MAX_PATH_W];
static wchar_t system_dir[MAX_PATH_W], bootstrap[MAX_PATH_W], dlls[MAX_PATH_W], stdlib[MAX_PATH_W], imports[3 * MAX_PATH_W];

static int join(wchar_t *out, size_t cap, const wchar_t *dir, const wchar_t *tail) {
    int result = swprintf(out, cap, L"%ls\\%ls", dir, tail);
    return result > 0 && (size_t)result < cap;
}

typedef void (__cdecl *PySetPath)(const wchar_t *);
typedef int (__cdecl *PyMain)(int, wchar_t **);

/* Resolve only from the protected, absolute DLL. Linking the CPython import library
   would load it before the program could restrict Windows' DLL search directories. */
static int function(HMODULE module, const char *name, void *dest, size_t size) {
    FARPROC p = GetProcAddress(module, name);
    if (!p || size != sizeof(p)) return 0;
    memcpy(dest, &p, size);
    return 1;
}

int wmain(int argc, wchar_t **argv) {
    if (argc != 5 && argc != 7) return SOURCE_REFUSED;
    DWORD n = GetModuleFileNameW(NULL, root, MAX_PATH_W);
    if (n == 0 || n >= MAX_PATH_W || !wcsrchr(root, L'\\')) return SOURCE_REFUSED;
    *wcsrchr(root, L'\\') = L'\0';
    if (!join(runtime, MAX_PATH_W, root, L"runtime") || !join(python, MAX_PATH_W, runtime, L"python.exe") ||
        !join(python_dll, MAX_PATH_W, runtime, L"python314.dll") ||
        !join(bootstrap, MAX_PATH_W, root, L"fixed_entry.py") ||
        !join(dlls, MAX_PATH_W, runtime, L"DLLs") || !join(stdlib, MAX_PATH_W, runtime, L"Lib"))
        return SOURCE_REFUSED;
    int used = swprintf(imports, 3 * MAX_PATH_W, L"%ls;%ls;%ls", stdlib, dlls, runtime);
    if (used <= 0 || used >= 3 * MAX_PATH_W) return SOURCE_REFUSED;

    n = GetSystemDirectoryW(system_dir, MAX_PATH_W);
    if (n == 0 || n >= MAX_PATH_W || !SetEnvironmentVariableW(L"PAL_HERMES_SYSTEM32", system_dir))
        return SOURCE_REFUSED;
    if (!SetDefaultDllDirectories(LOAD_LIBRARY_SEARCH_SYSTEM32 | LOAD_LIBRARY_SEARCH_USER_DIRS) ||
        !AddDllDirectory(runtime) || !AddDllDirectory(dlls) || !SetCurrentDirectoryW(root))
        return SOURCE_REFUSED;
    /* No child can outlive the timeout or the job-window owner's kill of this process. */
    DWORD no_children = 1;
    if (!SetProcessMitigationPolicy(ProcessChildProcessPolicy, &no_children, sizeof(no_children)))
        return SOURCE_REFUSED;
    HMODULE lib = LoadLibraryExW(python_dll, NULL, LOAD_LIBRARY_SEARCH_DLL_LOAD_DIR |
                                  LOAD_LIBRARY_SEARCH_SYSTEM32 | LOAD_LIBRARY_SEARCH_USER_DIRS);
    PySetPath set_home, set_path, set_program;
    PyMain run;
    if (!lib || !function(lib, "Py_SetPythonHome", &set_home, sizeof(set_home)) ||
        !function(lib, "Py_SetPath", &set_path, sizeof(set_path)) ||
        !function(lib, "Py_SetProgramName", &set_program, sizeof(set_program)) ||
        !function(lib, "Py_Main", &run, sizeof(run))) return SOURCE_REFUSED;

    set_program(python);
    set_home(runtime);
    set_path(imports);
    wchar_t *args[12] = {python, L"-I", L"-S", L"-B", bootstrap};
    for (int i = 1; i < argc; ++i) args[i + 4] = argv[i];
    return run(argc + 4, args);
}
