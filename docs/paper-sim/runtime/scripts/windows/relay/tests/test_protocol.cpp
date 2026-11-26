#include "../src/protocol.h"
#include <algorithm>
#include <cassert>
#include <limits>

int main() {
    using namespace relay;
    std::string ok = "0123456789abcdef0123456789abcdef";
    assert(valid_nonce(ok));
    for (const std::string& invalid : {std::string{}, ok.substr(1), ok+"0", ok.substr(1)+"A",
                                      ok.substr(1)+"\n", std::string("--serve"),
                                      std::string(31,'0')+char(0), ok.substr(1)+" "})
        assert(!valid_nonce(invalid));
    for (uint32_t n : {uint32_t(0), MaxRequest, MaxReply, std::numeric_limits<uint32_t>::max()}) {
        std::array<unsigned char,4> encoded{};
        put32(encoded.data(), n);
        assert(get32(encoded.data()) == n);
    }
    std::array<unsigned char,4> example{};
    put32(example.data(), 0x01020304);
    assert((example == std::array<unsigned char,4>{{4,3,2,1}}));
    for (uint32_t i = 0; i <= 255; ++i) {
        bool empty_allowed = (i >= 21 && i <= 29) || i == 31;
        bool body_allowed = i == 0 || i == 32;
        assert(response_allowed(i, 0) == empty_allowed);
        assert(response_allowed(i, MaxReply) == body_allowed);
        assert(!response_allowed(i, MaxReply+1));
    }
    assert(RequestTag != ReplyTag && MaxRequest == 20480 && MaxReply == 8192);
    auto refuses = [](auto request) {
        try { request(); } catch (const std::runtime_error&) { return true; }
        return false;
    };
    const std::wstring ps = LR"(C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe)";
    const std::wstring script = LR"(C:\Program Files\PolymarketAlphaLab\WeixinDelivery\Invoke-PaperWeixin.ps1)";
    assert(fixed_invocation(ps, script, ok) ==
           LR"argv("C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe" -NoProfile -NonInteractive -ExecutionPolicy RemoteSigned -File "C:\Program Files\PolymarketAlphaLab\WeixinDelivery\Invoke-PaperWeixin.ps1" 0123456789abcdef0123456789abcdef)argv");
    assert(refuses([&] { fixed_invocation(ps, script, "--serve"); }));
    assert(refuses([&] { fixed_invocation(ps, script, ok+" extra"); }));
    assert(refuses([&] { fixed_invocation(ps, L"invalid\"path", ok); }));
    for (uint32_t size : {uint32_t(1), MaxRequest}) {
        auto header = request_header(ok, size);
        auto read = read_request(header);
        assert(read.nonce == ok && read.size == size);
        header[3] = 2;
        assert(refuses([&] { read_request(header); }));
    }
    auto input = request_header(ok, 1);
    for (uint32_t size : {uint32_t(0), MaxRequest+1, UINT32_MAX}) {
        put32(input.data()+4, size);
        assert(refuses([&] { read_request(input); }));
    }
    input = request_header(ok, 1);
    input[8] = '/';
    assert(refuses([&] { read_request(input); }));
    for (auto code : {0u, 21u, 32u}) {
        uint32_t size = code == 21 ? 0 : MaxReply;
        auto header = reply_header(code, size);
        auto parsed = read_reply(header);
        assert(parsed.code == code && parsed.size == size);
        header[0] = 'X';
        assert(refuses([&] { read_reply(header); }));
    }
    auto output = reply_header(0, 1);
    put32(output.data()+4, TransportFailure);
    assert(refuses([&] { read_reply(output); }));
}
