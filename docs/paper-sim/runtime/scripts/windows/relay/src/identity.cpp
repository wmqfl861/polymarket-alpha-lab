#include "relay_win.h"

namespace relay {
namespace {
struct TokenFacts { Bytes sid; bool ordinary; };
Bytes token_info(HANDLE token, TOKEN_INFORMATION_CLASS kind) {
    DWORD size = 0;
    GetTokenInformation(token, kind, nullptr, 0, &size);
    require(GetLastError() == ERROR_INSUFFICIENT_BUFFER && size > 0 && size <= 65536);
    Bytes data(size);
    require(GetTokenInformation(token, kind, data.data(), size, &size) != FALSE);
    return data;
}
Handle process_token(HANDLE process) {
    HANDLE raw = nullptr;
    require(OpenProcessToken(process, TOKEN_QUERY, &raw) != FALSE);
    return checked(raw);
}
TokenFacts facts(HANDLE token) {
    auto user = token_info(token, TokenUser);
    PSID sid = reinterpret_cast<TOKEN_USER*>(user.data())->User.Sid;
    require(IsValidSid(sid) != FALSE);
    Bytes id(GetLengthSid(sid));
    require(CopySid(static_cast<DWORD>(id.size()), id.data(), sid) != FALSE);
    auto level = token_info(token, TokenIntegrityLevel);
    PSID label = reinterpret_cast<TOKEN_MANDATORY_LABEL*>(level.data())->Label.Sid;
    require(IsValidSid(label) && *GetSidSubAuthorityCount(label) > 0);
    auto rid = *GetSidSubAuthority(label, *GetSidSubAuthorityCount(label) - 1);
    auto elevated = token_info(token, TokenElevation);
    auto ui = token_info(token, TokenUIAccess);
    bool ordinary = !reinterpret_cast<TOKEN_ELEVATION*>(elevated.data())->TokenIsElevated &&
                    *reinterpret_cast<const DWORD*>(ui.data()) == 0 &&
                    rid >= SECURITY_MANDATORY_MEDIUM_RID && rid < SECURITY_MANDATORY_HIGH_RID;
    return {id, ordinary};
}
bool equal_path(const std::wstring& a, const std::wstring& b) {
    return CompareStringOrdinal(a.c_str(), -1, b.c_str(), -1, TRUE) == CSTR_EQUAL;
}
bool account_sid(PSID sid) {
    if (!IsValidSid(sid) || *GetSidSubAuthorityCount(sid) != 5) return false;
    SID_IDENTIFIER_AUTHORITY authority = SECURITY_NT_AUTHORITY;
    return std::equal(std::begin(authority.Value), std::end(authority.Value),
                      GetSidIdentifierAuthority(sid)->Value) && *GetSidSubAuthority(sid, 0) == SECURITY_NT_NON_UNIQUE;
}
struct FileSecurity {
    PSID owner = nullptr;
    PACL dacl = nullptr;
    LocalPtr sd;
    explicit FileSecurity(HANDLE file) {
        PSECURITY_DESCRIPTOR raw = nullptr;
        DWORD e = GetSecurityInfo(file, SE_FILE_OBJECT,
            OWNER_SECURITY_INFORMATION | GROUP_SECURITY_INFORMATION | DACL_SECURITY_INFORMATION,
            &owner, nullptr, &dacl, nullptr, &raw);
        sd.reset(raw);
        require(e == ERROR_SUCCESS && raw && owner && dacl && IsValidAcl(dacl));
    }
};
void require_installed_account(HANDLE image, const Bytes& user) {
    FileSecurity fs(image);
    require(IsWellKnownSid(fs.owner, WinBuiltinAdministratorsSid) || IsWellKnownSid(fs.owner, WinLocalSystemSid));
    SECURITY_DESCRIPTOR_CONTROL ctl = 0;
    DWORD revision = 0;
    require(GetSecurityDescriptorControl(fs.sd.get(), &ctl, &revision) && (ctl & SE_DACL_PROTECTED));
    unsigned grants = 0;
    for (DWORD i = 0; i < fs.dacl->AceCount; ++i) {
        void* p = nullptr;
        require(GetAce(fs.dacl, i, &p) != FALSE);
        auto* ace = static_cast<ACCESS_ALLOWED_ACE*>(p);
        require(ace->Header.AceType == ACCESS_ALLOWED_ACE_TYPE && ace->Header.AceFlags == 0);
        PSID sid = &ace->SidStart;
        require(IsValidSid(sid) != FALSE);
        if (IsWellKnownSid(sid, WinLocalSystemSid) || IsWellKnownSid(sid, WinBuiltinAdministratorsSid))
            continue;
        require(account_sid(sid) && EqualSid(sid, const_cast<unsigned char*>(user.data())) &&
                (ace->Mask & (FILE_READ_DATA | FILE_EXECUTE)) == (FILE_READ_DATA | FILE_EXECUTE) &&
                !(ace->Mask & ~(FILE_GENERIC_READ | FILE_GENERIC_EXECUTE)));
        ++grants;
    }
    require(grants == 1);
}
class EffectiveRights {
    AUTHZ_RESOURCE_MANAGER_HANDLE rm_ = nullptr;
    AUTHZ_CLIENT_CONTEXT_HANDLE context_ = nullptr;
public:
    explicit EffectiveRights(HANDLE token) {
        require(AuthzInitializeResourceManager(AUTHZ_RM_FLAG_NO_AUDIT, nullptr, nullptr, nullptr, nullptr, &rm_) != FALSE);
        if (!AuthzInitializeContextFromToken(0, token, rm_, nullptr, LUID{}, nullptr, &context_)) {
            AuthzFreeResourceManager(rm_);
            throw std::runtime_error("rights unavailable");
        }
    }
    ~EffectiveRights() { AuthzFreeContext(context_); AuthzFreeResourceManager(rm_); }
    void read_only(HANDLE file, bool directory, bool install_directory) {
        FileSecurity security(file);
        AUTHZ_ACCESS_REQUEST request{};
        request.DesiredAccess = MAXIMUM_ALLOWED;
        DWORD mask = 0, error = 0;
        AUTHZ_ACCESS_REPLY reply{};
        reply.ResultListLength = 1;
        reply.GrantedAccessMask = &mask;
        reply.Error = &error;
        require(AuthzAccessCheck(0, context_, &request, nullptr, security.sd.get(), nullptr, 0, &reply, nullptr) != FALSE);
        require(error == ERROR_SUCCESS);
        DWORD unsafe = DELETE | WRITE_DAC | WRITE_OWNER;
        if (directory) {
            unsafe |= FILE_DELETE_CHILD;
            if (install_directory) unsafe |= FILE_ADD_FILE | FILE_ADD_SUBDIRECTORY | FILE_WRITE_ATTRIBUTES;
        } else {
            unsafe |= FILE_WRITE_DATA | FILE_APPEND_DATA | FILE_WRITE_EA | FILE_WRITE_ATTRIBUTES;
        }
        require(!(mask & unsafe));
    }
};
Handle pin(const std::wstring& path, bool directory, bool read_content = false) {
    DWORD access = FILE_READ_ATTRIBUTES | READ_CONTROL | (read_content ? GENERIC_READ : 0);
    DWORD flags = FILE_FLAG_OPEN_REPARSE_POINT | (directory ? FILE_FLAG_BACKUP_SEMANTICS : 0);
    auto file = checked(CreateFileW(path.c_str(), access, FILE_SHARE_READ | (directory ? FILE_SHARE_WRITE : 0),
                                    nullptr, OPEN_EXISTING, flags, nullptr));
    BY_HANDLE_FILE_INFORMATION info{};
    require(GetFileInformationByHandle(file.get(), &info) &&
            !(info.dwFileAttributes & FILE_ATTRIBUTE_REPARSE_POINT) &&
            bool(info.dwFileAttributes & FILE_ATTRIBUTE_DIRECTORY) == directory);
    wchar_t canonical[32768];
    DWORD size = GetFinalPathNameByHandleW(file.get(), canonical, 32768, FILE_NAME_NORMALIZED | VOLUME_NAME_DOS);
    require(size > 0 && size < 32768 && equal_path(canonical, L"\\\\?\\" + path));
    return file;
}
void pin_parents(const std::wstring& file, std::vector<Handle>& pinned, EffectiveRights* rights,
                 const std::wstring& install_path) {
    require(file.size() > 3 && file[1] == L':' && file[2] == L'\\');
    require(GetDriveTypeW(file.substr(0, 3).c_str()) == DRIVE_FIXED);
    for (size_t i = 2; i != std::wstring::npos; i = file.find(L'\\', i + 1)) {
        auto component = file.substr(0, i == 2 ? 3 : i);
        Handle dir = pin(component, true);
        if (rights) rights->read_only(dir.get(), true, equal_path(component, install_path));
        pinned.push_back(std::move(dir));
    }
}
void require_process_image(HANDLE process, const std::wstring& expected, HANDLE installed) {
    wchar_t path[32768];
    DWORD size = 32768;
    require(QueryFullProcessImageNameW(process, 0, path, &size) && equal_path(std::wstring(path, size), expected));
    auto image = pin(expected, false);
    BY_HANDLE_FILE_INFORMATION a{}, b{};
    require(GetFileInformationByHandle(image.get(), &a) && GetFileInformationByHandle(installed, &b) &&
            a.dwVolumeSerialNumber == b.dwVolumeSerialNumber && a.nFileIndexHigh == b.nFileIndexHigh &&
            a.nFileIndexLow == b.nFileIndexLow);
}
}
std::wstring folder(REFKNOWNFOLDERID id) {
    PWSTR path = nullptr;
    HRESULT hr = SHGetKnownFolderPath(id, KF_FLAG_DEFAULT, nullptr, &path);
    std::unique_ptr<wchar_t, decltype(&CoTaskMemFree)> owner(path, CoTaskMemFree);
    require(SUCCEEDED(hr) && path && *path);
    return path;
}
std::wstring current_sid_sddl(const Bytes& id) {
    LPWSTR raw = nullptr;
    require(ConvertSidToStringSidW(const_cast<unsigned char*>(id.data()), &raw) != FALSE);
    LocalPtr owner(raw);
    return raw;
}
Installation::Installation(bool worker) {
    auto token = process_token(GetCurrentProcess());
    TokenFacts this_user = facts(token.get());
    account = this_user.sid;
    require(!worker || this_user.ordinary);
    auto dir = folder(FOLDERID_ProgramFiles) + L"\\PolymarketAlphaLab\\WeixinDelivery";
    self = dir + L"\\PaperWeixinRelay.exe";
    script = dir + L"\\Invoke-PaperWeixin.ps1";
    std::unique_ptr<EffectiveRights> rights;
    if (worker) rights.reset(new EffectiveRights(token.get()));
    pin_parents(self, pins, rights.get(), dir);
    installed_image = pin(self, false);
    require_installed_account(installed_image.get(), account);
    require_process_image(GetCurrentProcess(), self, installed_image.get());
    if (!worker) return;
    rights->read_only(installed_image.get(), false, false);
    auto fixed_script = pin(script, false, true);
    rights->read_only(fixed_script.get(), false, false);
    verify_script_sha(fixed_script.get());
    pins.push_back(std::move(fixed_script));
    auto binding = pin(dir + L"\\weixin-binding.json", false);
    rights->read_only(binding.get(), false, false);
    pins.push_back(std::move(binding));  // Metadata handle only; relay cannot read binding contents.
    wchar_t system_path[MAX_PATH + 1];
    UINT n = GetSystemDirectoryW(system_path, MAX_PATH);
    require(n > 0 && n < MAX_PATH);
    system = system_path;
    powershell = system + L"\\WindowsPowerShell\\v1.0\\powershell.exe";
    pin_parents(powershell, pins, rights.get(), dir);
    auto ps = pin(powershell, false);
    rights->read_only(ps.get(), false, false);
    pins.push_back(std::move(ps));
}
Handle authenticated_peer(HANDLE pipe, const Installation& installation, bool server_peer) {
    ULONG pid = 0, recheck = 0;
    auto get_pid = server_peer ? GetNamedPipeServerProcessId : GetNamedPipeClientProcessId;
    require(get_pid(pipe, &pid) && pid != 0);
    auto peer = checked(OpenProcess(SYNCHRONIZE | PROCESS_QUERY_LIMITED_INFORMATION, FALSE, pid));
    require(WaitForSingleObject(peer.get(), 0) == WAIT_TIMEOUT && GetProcessId(peer.get()) == pid);
    require_process_image(peer.get(), installation.self, installation.installed_image.get());
    auto token = process_token(peer.get());
    auto info = facts(token.get());
    require(EqualSid(info.sid.data(), const_cast<unsigned char*>(installation.account.data())) &&
            (!server_peer || info.ordinary));
    require(get_pid(pipe, &recheck) && recheck == pid && WaitForSingleObject(peer.get(), 0) == WAIT_TIMEOUT);
    return peer;
}
void verify_script_sha(HANDLE file) {
    BCRYPT_ALG_HANDLE algorithm = nullptr;
    BCRYPT_HASH_HANDLE hash = nullptr;
    require(BCryptOpenAlgorithmProvider(&algorithm, BCRYPT_SHA256_ALGORITHM, nullptr, 0) == 0);
    try {
        require(BCryptCreateHash(algorithm, &hash, nullptr, 0, nullptr, 0, 0) == 0);
        DWORD n = 0;
        std::array<unsigned char, 8192> part{};
        unsigned total = 0;
        do {
            require(ReadFile(file, part.data(), static_cast<DWORD>(part.size()), &n, nullptr) != FALSE);
            total += n;
            require(total <= 262144 && BCryptHashData(hash, part.data(), n, 0) == 0);
        } while (n);
        std::array<unsigned char, 32> digest{};
        require(BCryptFinishHash(hash, digest.data(), 32, 0) == 0);
        static constexpr unsigned char expected[] = {
            0xe8,0x6b,0xec,0xa8,0xf4,0x07,0x3d,0xa7,0x5a,0x8b,0xc1,0xcd,0x3a,0xee,0xf3,0xe8,
            0x52,0x30,0x41,0x7c,0x0b,0x19,0xf9,0x42,0x44,0x32,0xe7,0x9b,0xaa,0x79,0x98,0xc6};
        require(std::equal(digest.begin(), digest.end(), expected));
    } catch (...) {
        if (hash) BCryptDestroyHash(hash);
        BCryptCloseAlgorithmProvider(algorithm, 0);
        throw;
    }
    BCryptDestroyHash(hash);
    BCryptCloseAlgorithmProvider(algorithm, 0);
}
}
