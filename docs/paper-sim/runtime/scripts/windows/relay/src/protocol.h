#pragma once
#include <algorithm>
#include <array>
#include <cstdint>
#include <stdexcept>
#include <string>
#include <vector>

namespace relay {
using Bytes = std::vector<unsigned char>;
constexpr size_t NonceBytes = 32;
constexpr uint32_t MaxRequest = 20480;
constexpr uint32_t MaxReply = 8192;
constexpr uint32_t TransportFailure = 70;  // Outside the original script's refusal codes.
constexpr uint32_t MaxMillis = 575000;     // Existing HC maximum. Shorter HC deadlines still win.
constexpr std::array<unsigned char, 4> RequestTag{{'W', 'X', 'Q', 1}}, ReplyTag{{'W', 'X', 'R', 1}};

inline void require(bool ok) { if (!ok) throw std::runtime_error("relay refused"); }
inline bool valid_nonce(const std::string& text) {
    if (text.size() != NonceBytes) return false;
    for (char c : text) if (!((c >= '0' && c <= '9') || (c >= 'a' && c <= 'f'))) return false;
    return true;
}
inline std::wstring fixed_invocation(const std::wstring& system_powershell,
                                     const std::wstring& installed_script, const std::string& nonce) {
    require(valid_nonce(nonce) && system_powershell.find(L'"') == std::wstring::npos &&
            installed_script.find(L'"') == std::wstring::npos);
    return L"\"" + system_powershell + L"\" -NoProfile -NonInteractive -ExecutionPolicy RemoteSigned -File \"" +
           installed_script + L"\" " + std::wstring(nonce.begin(), nonce.end());
}
inline void put32(unsigned char* p, uint32_t n) {
    for (unsigned i = 0; i < 4; ++i) p[i] = static_cast<unsigned char>(n >> (8 * i));
}
inline uint32_t get32(const unsigned char* p) {
    uint32_t v = 0;
    for (unsigned i = 0; i < 4; ++i) v |= uint32_t(p[i]) << (8 * i);
    return v;
}
inline bool response_allowed(uint32_t code, size_t bytes) {
    if (bytes > MaxReply) return false;
    if (code == 0 || code == 32) return bytes > 0;
    return bytes == 0 && ((code >= 21 && code <= 29) || code == 31);
}
struct Reply { uint32_t code; Bytes body; };
using RequestHeader = std::array<unsigned char, 8 + NonceBytes>;
using ReplyHeader = std::array<unsigned char, 12>;
struct RequestMeta { std::string nonce; uint32_t size; };
struct ReplyMeta { uint32_t code, size; };
inline RequestHeader request_header(const std::string& nonce, uint32_t size) {
    require(valid_nonce(nonce) && size && size <= MaxRequest);
    RequestHeader h{};
    std::copy(RequestTag.begin(), RequestTag.end(), h.begin());
    put32(h.data()+4, size);
    std::copy(nonce.begin(), nonce.end(), h.begin()+8);
    return h;
}
inline RequestMeta read_request(const RequestHeader& h) {
    require(std::equal(RequestTag.begin(), RequestTag.end(), h.begin()));
    RequestMeta request{std::string(h.begin()+8, h.end()), get32(h.data()+4)};
    require(valid_nonce(request.nonce) && request.size && request.size <= MaxRequest);
    return request;
}
inline ReplyHeader reply_header(uint32_t code, uint32_t size) {
    require(response_allowed(code, size));
    ReplyHeader h{};
    std::copy(ReplyTag.begin(), ReplyTag.end(), h.begin());
    put32(h.data()+4, code);
    put32(h.data()+8, size);
    return h;
}
inline ReplyMeta read_reply(const ReplyHeader& h) {
    require(std::equal(ReplyTag.begin(), ReplyTag.end(), h.begin()));
    ReplyMeta reply{get32(h.data()+4), get32(h.data()+8)};
    require(response_allowed(reply.code, reply.size));
    return reply;
}
}
