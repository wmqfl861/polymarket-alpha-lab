#include "relay_win.h"

namespace relay {
constexpr wchar_t PipeName[] = L"\\\\.\\pipe\\PolymarketAlphaLab.FixedWeixin.v1";
constexpr unsigned char ReadAck = 0x51;

uint64_t now() { return GetTickCount64(); }
DWORD remaining(uint64_t until) {
    uint64_t n = now();
    return n >= until ? 0 : static_cast<DWORD>(std::min<uint64_t>(until - n, MaxMillis));
}
void pipe_io(HANDLE pipe, bool write, void* bytes, DWORD count, uint64_t until) {
    auto* data = static_cast<unsigned char*>(bytes);
    auto event = checked(CreateEventW(nullptr, TRUE, FALSE, nullptr));
    while (count) {
        require(remaining(until) != 0 && ResetEvent(event.get()));
        OVERLAPPED ov{};
        ov.hEvent = event.get();
        DWORD transferred = 0;
        BOOL done = write ? WriteFile(pipe, data, count, &transferred, &ov)
                          : ReadFile(pipe, data, count, &transferred, &ov);
        if (!done) {
            require(GetLastError() == ERROR_IO_PENDING);
            if (WaitForSingleObject(event.get(), remaining(until)) != WAIT_OBJECT_0) {
                CancelIoEx(pipe, &ov);
                GetOverlappedResult(pipe, &ov, &transferred, TRUE);
                throw std::runtime_error("transport deadline");
            }
            require(GetOverlappedResult(pipe, &ov, &transferred, FALSE) != FALSE);
        }
        require(transferred > 0 && transferred <= count);
        data += transferred;
        count -= transferred;
    }
}
namespace {
Bytes stdin_bytes(uint64_t until) {
    HANDLE input = GetStdHandle(STD_INPUT_HANDLE);
    require(input && input != INVALID_HANDLE_VALUE);
    DWORD type = GetFileType(input);
    require(type == FILE_TYPE_DISK || type == FILE_TYPE_PIPE);
    Bytes request;
    std::array<unsigned char, 2048> part{};
    for (;;) {
        require(remaining(until) != 0);
        DWORD available = static_cast<DWORD>(part.size());
        if (type == FILE_TYPE_PIPE) {
            if (!PeekNamedPipe(input, nullptr, 0, nullptr, &available, nullptr)) {
                require(GetLastError() == ERROR_BROKEN_PIPE); break;
            }
            if (!available) { Sleep(2); continue; }
        }
        DWORD read = 0;
        if (!ReadFile(input, part.data(), std::min<DWORD>(available, static_cast<DWORD>(part.size())), &read, nullptr)) {
            require(GetLastError() == ERROR_BROKEN_PIPE); break;
        }
        if (!read) break;
        require(read <= MaxRequest - request.size());
        request.insert(request.end(), part.begin(), part.begin() + read);
    }
    require(!request.empty());
    return request;
}
DWORD WINAPI terminate_expired_client(void*) {
    Sleep(MaxMillis);
    TerminateProcess(GetCurrentProcess(), TransportFailure);
    return 0;
}
int call(const Installation& install, const std::string& nonce) {
    auto watchdog = checked(CreateThread(nullptr, 0, terminate_expired_client, nullptr, 0, nullptr));
    uint64_t until = now() + MaxMillis;
    auto bytes = stdin_bytes(now() + 3000);
    auto pipe = checked(CreateFileW(PipeName, FILE_READ_DATA | FILE_WRITE_DATA | SYNCHRONIZE, 0, nullptr,
                                    OPEN_EXISTING, FILE_FLAG_OVERLAPPED | SECURITY_SQOS_PRESENT | SECURITY_ANONYMOUS, nullptr));
    auto peer = authenticated_peer(pipe.get(), install, true);
    auto header = request_header(nonce, static_cast<uint32_t>(bytes.size()));
    uint64_t writing_until = std::min(until, now() + 3000);
    pipe_io(pipe.get(), true, header.data(), static_cast<DWORD>(header.size()), writing_until);
    pipe_io(pipe.get(), true, bytes.data(), static_cast<DWORD>(bytes.size()), writing_until);
    ReplyHeader reply{};
    pipe_io(pipe.get(), false, reply.data(), static_cast<DWORD>(reply.size()), until);
    auto [code, length] = read_reply(reply);
    Bytes output(length);
    pipe_io(pipe.get(), false, output.data(), length, until);
    unsigned char ack = ReadAck;
    pipe_io(pipe.get(), true, &ack, 1, std::min(until, now() + 1000));
    if (!output.empty()) {
        DWORD wrote = 0;
        require(WriteFile(GetStdHandle(STD_OUTPUT_HANDLE), output.data(), length, &wrote, nullptr) && wrote == length);
    }
    return static_cast<int>(code);
}
bool wait_connection(HANDLE pipe) {
    auto event = checked(CreateEventW(nullptr, TRUE, FALSE, nullptr));
    OVERLAPPED ov{}; ov.hEvent = event.get();
    if (ConnectNamedPipe(pipe, &ov)) return true;
    DWORD code = GetLastError();
    if (code == ERROR_PIPE_CONNECTED) return true;
    if (code == ERROR_NO_DATA || code == ERROR_PIPE_NOT_CONNECTED) return false;
    require(code == ERROR_IO_PENDING);
    DWORD ignored = 0;
    if (GetOverlappedResult(pipe, &ov, &ignored, TRUE)) return true;
    code = GetLastError();
    require(code == ERROR_NO_DATA || code == ERROR_PIPE_NOT_CONNECTED || code == ERROR_BROKEN_PIPE);
    return false;
}
void transfer(HANDLE pipe, const Installation& install) {
    uint64_t until = now() + MaxMillis;
    auto peer = authenticated_peer(pipe, install, false);
    RequestHeader input{};
    uint64_t read_until = now() + 3000;
    pipe_io(pipe, false, input.data(), static_cast<DWORD>(input.size()), read_until);
    auto [nonce, length] = read_request(input);
    Bytes request(length);
    pipe_io(pipe, false, request.data(), length, read_until);
    auto reply = fixed_child(install, nonce, request, pipe, peer.get(), until);
    auto header = reply_header(reply.code, static_cast<uint32_t>(reply.body.size()));
    uint64_t finishing = std::min(until, now() + 2000);
    pipe_io(pipe, true, header.data(), static_cast<DWORD>(header.size()), finishing);
    pipe_io(pipe, true, reply.body.data(), static_cast<DWORD>(reply.body.size()), finishing);
    unsigned char ack = 0;
    pipe_io(pipe, false, &ack, 1, finishing);  // Keeps the reply alive until the client read it.
    require(ack == ReadAck);
}
int serve(const Installation& install) {
    std::wstring dacl = L"D:P(A;;0x00100003;;;" + current_sid_sddl(install.account) + L")";
    PSECURITY_DESCRIPTOR descriptor = nullptr;
    require(ConvertStringSecurityDescriptorToSecurityDescriptorW(dacl.c_str(), SDDL_REVISION_1,
                                                                 &descriptor, nullptr) != FALSE);
    LocalPtr keep(descriptor);
    SECURITY_ATTRIBUTES sa{sizeof(sa), descriptor, FALSE};
    auto pipe = checked(CreateNamedPipeW(PipeName, PIPE_ACCESS_DUPLEX | FILE_FLAG_OVERLAPPED | FILE_FLAG_FIRST_PIPE_INSTANCE,
                             PIPE_TYPE_BYTE | PIPE_READMODE_BYTE | PIPE_WAIT | PIPE_REJECT_REMOTE_CLIENTS,
                             1, MaxReply + 16, MaxRequest + 64, 0, &sa));
    for (;;) {
        if (wait_connection(pipe.get())) {
            try { transfer(pipe.get(), install); } catch (const std::exception&) { /* Local refusal. */ }
        }
        if (!DisconnectNamedPipe(pipe.get())) {
            DWORD error = GetLastError();
            require(error == ERROR_PIPE_NOT_CONNECTED || error == ERROR_NO_DATA || error == ERROR_BROKEN_PIPE);
        }
    }
}
}
}
int wmain(int argc, wchar_t** argv) {
    try {
        relay::require(SetDefaultDllDirectories(LOAD_LIBRARY_SEARCH_SYSTEM32) != FALSE && argc == 2);
        relay::require(SUCCEEDED(CoInitializeEx(nullptr, COINIT_MULTITHREADED)));
        std::wstring arg(argv[1]);
        if (arg == L"--serve") return relay::serve(relay::Installation(true));
        relay::require(arg.size() == relay::NonceBytes && std::all_of(arg.begin(), arg.end(), [](wchar_t c) {
            return (c >= L'0' && c <= L'9') || (c >= L'a' && c <= L'f');
        }));
        return relay::call(relay::Installation(false), std::string(arg.begin(), arg.end()));
    } catch (...) { return relay::TransportFailure; }
}
