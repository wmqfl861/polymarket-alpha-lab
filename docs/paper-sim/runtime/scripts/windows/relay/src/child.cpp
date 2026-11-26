#include "relay_win.h"
#include <thread>

namespace relay {
namespace {
void append(std::vector<wchar_t>& block, const std::wstring& name, const std::wstring& value) {
    std::wstring pair = name + L"=" + value;
    block.insert(block.end(), pair.begin(), pair.end());
    block.push_back(0);
}
std::vector<wchar_t> child_environment(const Installation& installed) {
    // Derive the original user's directories from Windows, never from a request or HC environment.
    std::vector<wchar_t> result;
    const auto local = folder(FOLDERID_LocalAppData);
    const auto windows = folder(FOLDERID_Windows);
    const auto shell = installed.system + L"\\WindowsPowerShell\\v1.0";
    // GetTempPath is also what the original script uses to find legacy receipts.
    // The registered worker's local account supplies the path; no request field transports it.
    wchar_t storage[32768];
    DWORD count = GetTempPathW(32768, storage);
    require(count > 3 && count < 32768);
    std::wstring temp(storage, count);
    require(temp[1] == L':' && temp[2] == L'\\' && GetDriveTypeW(temp.substr(0, 3).c_str()) == DRIVE_FIXED);
    append(result, L"APPDATA", folder(FOLDERID_RoamingAppData));
    append(result, L"ComSpec", installed.system + L"\\cmd.exe");
    append(result, L"LOCALAPPDATA", local);
    append(result, L"PATH", installed.system + L";" + windows + L";" + shell);
    append(result, L"ProgramData", folder(FOLDERID_ProgramData));
    append(result, L"ProgramFiles", folder(FOLDERID_ProgramFiles));
    append(result, L"PSModulePath", shell + L"\\Modules");
    append(result, L"SystemRoot", windows);
    append(result, L"TEMP", temp);
    append(result, L"TMP", temp);
    append(result, L"USERPROFILE", folder(FOLDERID_Profile));
    append(result, L"WINDIR", windows);
    result.push_back(0);
    return result;
}
struct Direction {
    Handle read, write;
    explicit Direction(SECURITY_ATTRIBUTES& sa) {
        HANDLE r = nullptr, w = nullptr;
        require(CreatePipe(&r, &w, &sa, MaxRequest + 1) != FALSE);
        read = Handle(r); write = Handle(w);
    }
};
class InheritedHandles {
    std::vector<unsigned char> storage;
    LPPROC_THREAD_ATTRIBUTE_LIST list_;
public:
    InheritedHandles(HANDLE (&handles)[3], HANDLE& job) {
        SIZE_T bytes = 0;
        InitializeProcThreadAttributeList(nullptr, 2, 0, &bytes);
        require(GetLastError() == ERROR_INSUFFICIENT_BUFFER && bytes > 0);
        storage.resize(bytes);
        list_ = reinterpret_cast<LPPROC_THREAD_ATTRIBUTE_LIST>(storage.data());
        require(InitializeProcThreadAttributeList(list_, 2, 0, &bytes) != FALSE);
        if (!UpdateProcThreadAttribute(list_, 0, PROC_THREAD_ATTRIBUTE_HANDLE_LIST, handles,
                                       sizeof(handles), nullptr, nullptr) ||
            !UpdateProcThreadAttribute(list_, 0, PROC_THREAD_ATTRIBUTE_JOB_LIST, &job,
                                       sizeof(job), nullptr, nullptr)) {
            DeleteProcThreadAttributeList(list_); throw std::runtime_error("handle list unavailable");
        }
    }
    ~InheritedHandles() { DeleteProcThreadAttributeList(list_); }
    LPPROC_THREAD_ATTRIBUTE_LIST get() const { return list_; }
};
bool peer_waiting(HANDLE pipe, HANDLE peer) {
    DWORD extra = 0;
    return WaitForSingleObject(peer, 0) == WAIT_TIMEOUT && PeekNamedPipe(pipe, nullptr, 0, nullptr, &extra, nullptr) && !extra;
}
bool drain(HANDLE pipe, Bytes& output) {
    std::array<unsigned char, 1024> part{};
    for (;;) {
        DWORD available = 0;
        if (!PeekNamedPipe(pipe, nullptr, 0, nullptr, &available, nullptr)) {
            require(GetLastError() == ERROR_BROKEN_PIPE); return true;
        }
        if (!available) return false;
        require(available <= MaxReply - output.size());
        DWORD got = 0;
        require(ReadFile(pipe, part.data(), std::min<DWORD>(available, static_cast<DWORD>(part.size())), &got, nullptr) && got);
        output.insert(output.end(), part.begin(), part.begin() + got);
    }
}
}
Reply fixed_child(const Installation& installed, const std::string& nonce, const Bytes& request,
                  HANDLE pipe, HANDLE peer, uint64_t until) {
    require(valid_nonce(nonce) && !request.empty() && request.size() <= MaxRequest && peer_waiting(pipe, peer));
    SECURITY_ATTRIBUTES sa{sizeof(sa), nullptr, TRUE};
    Direction input(sa), output(sa);
    require(SetHandleInformation(input.write.get(), HANDLE_FLAG_INHERIT, 0) &&
            SetHandleInformation(output.read.get(), HANDLE_FLAG_INHERIT, 0));
    auto discard = checked(CreateFileW(L"NUL", GENERIC_WRITE, FILE_SHARE_READ | FILE_SHARE_WRITE,
                                       &sa, OPEN_EXISTING, FILE_ATTRIBUTE_NORMAL, nullptr));
    auto job = checked(CreateJobObjectW(nullptr, nullptr));
    JOBOBJECT_EXTENDED_LIMIT_INFORMATION limits{};
    limits.BasicLimitInformation.LimitFlags = JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE;
    require(SetInformationJobObject(job.get(), JobObjectExtendedLimitInformation, &limits, sizeof(limits)) != FALSE);
    HANDLE inherited[]{input.read.get(), output.write.get(), discard.get()};
    HANDLE job_handle = job.get();
    InheritedHandles attrs(inherited, job_handle);
    STARTUPINFOEXW startup{};
    startup.StartupInfo.cb = sizeof(startup);
    startup.StartupInfo.dwFlags = STARTF_USESTDHANDLES;
    startup.StartupInfo.hStdInput = inherited[0];
    startup.StartupInfo.hStdOutput = inherited[1];
    startup.StartupInfo.hStdError = inherited[2];
    startup.lpAttributeList = attrs.get();
    PROCESS_INFORMATION child{};
    // All executable and option text is fixed. Only an already checked ASCII nonce is interpolated.
    std::wstring command = fixed_invocation(installed.powershell, installed.script, nonce);
    auto environment = child_environment(installed);
    require(remaining(until) && peer_waiting(pipe, peer));
    require(CreateProcessW(installed.powershell.c_str(), command.data(), nullptr, nullptr, TRUE,
                           CREATE_SUSPENDED | CREATE_NO_WINDOW | CREATE_UNICODE_ENVIRONMENT | EXTENDED_STARTUPINFO_PRESENT,
                           environment.data(), installed.system.c_str(), &startup.StartupInfo, &child) != FALSE);
    Handle process(child.hProcess), primary(child.hThread);
    BOOL contained = FALSE;
    if (!IsProcessInJob(process.get(), job.get(), &contained) || !contained) {
        TerminateProcess(process.get(), TransportFailure);
        throw std::runtime_error("job unavailable");
    }
    input.read.reset(); output.write.reset(); discard.reset();
    require(peer_waiting(pipe, peer) && ResumeThread(primary.get()) != DWORD(-1));
    // Closing the job cancels PowerShell and its descendants on any incomplete transport.
    // A send claim acquired inside the original script survives that cancellation.
    Bytes reply;
    bool wrote = false;
    std::thread writer([&] {
        DWORD sent = 0;
        wrote = WriteFile(input.write.get(), request.data(), static_cast<DWORD>(request.size()), &sent, nullptr) &&
                sent == request.size();
        input.write.reset();
    });
    DWORD code = TransportFailure;
    try {
        bool eof = false;
        bool ended = false;
        while (!ended || !eof) {
            require(remaining(until) && peer_waiting(pipe, peer));
            if (!eof) eof = drain(output.read.get(), reply);
            if (!ended) {
                DWORD state = WaitForSingleObject(process.get(), 10);
                require(state == WAIT_OBJECT_0 || state == WAIT_TIMEOUT);
                if (state == WAIT_OBJECT_0) {
                    require(GetExitCodeProcess(process.get(), &code) != FALSE);
                    ended = true;
                    job.reset();  // Any surviving descendant cannot hold these handles indefinitely.
                }
            } else if (!eof) Sleep(5);
        }
    } catch (...) {
        job.reset();  // Closes every child stdin reader before joining the writer.
        writer.join();
        throw;
    }
    writer.join();
    require(wrote && response_allowed(code, reply.size()));
    return {code, std::move(reply)};
}
}
