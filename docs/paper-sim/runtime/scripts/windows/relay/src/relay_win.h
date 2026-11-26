#pragma once
#ifndef _WIN64
#error Build the native x64 relay.
#endif
#ifndef _WIN32_WINNT
#define _WIN32_WINNT 0x0A00
#endif
#define WIN32_LEAN_AND_MEAN
#define NOMINMAX
#include <windows.h>
#include <sddl.h>
#include <aclapi.h>
#include <authz.h>
#include <shlobj.h>
#include <bcrypt.h>
#include <algorithm>
#include <array>
#include <memory>
#include <string>
#include <vector>
#include "protocol.h"

namespace relay {
class Handle {
    HANDLE value_ = nullptr;
public:
    Handle() = default;
    explicit Handle(HANDLE value) : value_(value) {}
    ~Handle() { reset(); }
    Handle(const Handle&) = delete;
    Handle& operator=(const Handle&) = delete;
    Handle(Handle&& other) noexcept : value_(other.release()) {}
    Handle& operator=(Handle&& other) noexcept { if (this != &other) { reset(); value_ = other.release(); } return *this; }
    HANDLE get() const { return value_; }
    explicit operator bool() const { return value_ && value_ != INVALID_HANDLE_VALUE; }
    HANDLE release() { HANDLE v = value_; value_ = nullptr; return v; }
    void reset() { if (*this) CloseHandle(value_); value_ = nullptr; }
};
inline Handle checked(HANDLE v) { Handle h(v); require(static_cast<bool>(h)); return h; }
struct LocalFreeDeleter { void operator()(void* p) const { if (p) LocalFree(p); } };
using LocalPtr = std::unique_ptr<void, LocalFreeDeleter>;

struct Installation {
    std::wstring self, script, powershell, system;
    Bytes account;
    std::vector<Handle> pins;
    Handle installed_image;
    explicit Installation(bool worker);
};
Handle authenticated_peer(HANDLE pipe, const Installation&, bool server_peer);
std::wstring current_sid_sddl(const Bytes&);
std::wstring folder(REFKNOWNFOLDERID id);
void verify_script_sha(HANDLE file);
uint64_t now();
DWORD remaining(uint64_t until);
Reply fixed_child(const Installation&, const std::string& nonce, const Bytes& request,
                  HANDLE pipe, HANDLE peer, uint64_t until);
}
