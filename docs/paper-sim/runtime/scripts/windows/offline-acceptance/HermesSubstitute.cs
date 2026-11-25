using System;
using System.IO;
using System.Text;

internal static class HermesSubstitute
{
    private const string Nonce = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa";
    private const string Id = "fx49-fixture-\"dm\"\\";
    private const string Message = "fx49-fixture-message";
    private static readonly UTF8Encoding Utf8 = new UTF8Encoding(false, true);

    public static int Main(string[] args)
    {
        try
        {
            string profile = Environment.GetEnvironmentVariable("LOCALAPPDATA");
            if (String.IsNullOrEmpty(profile) ||
                !File.Exists(Path.Combine(profile, "fx49-fixture-mode")))
            {
                return 90;
            }

            string mode = File.ReadAllText(Path.Combine(profile, "fx49-fixture-mode"), Utf8);
            if (Matches(args, new[] { "send", "--list", "weixin", "--json" }))
            {
                string listing;
                switch (mode)
                {
                    case "zero":
                        listing = "";
                        break;
                    case "multiple":
                        listing = Target(Id, "dm") + "," + Target("fx49-fixture-other", "dm");
                        break;
                    case "group":
                        listing = Target(Id, "group");
                        break;
                    case "wrong-digest":
                        listing = Target("fx49-fixture-other", "dm");
                        break;
                    default:
                        listing = Target(Id, "dm");
                        break;
                }
                Console.Write("{\"platforms\":{\"weixin\":[" + listing + "]}}");
                return 0;
            }

            if (args.Length < 2 || args[0] != "send" || args[1] == "--list")
            {
                return 91;
            }
            File.AppendAllText(Path.Combine(profile, "fx49-native-send-count"), "1\n", Utf8);
            string note = Path.Combine(profile, "PolymarketAlphaLab", "WeixinDelivery", "jobs", Nonce, "note.txt");
            if (!Matches(args, new[] { "send", "--to", "weixin:" + Id, "--file", note, "--json" }) ||
                !Matches(File.ReadAllBytes(note), File.ReadAllBytes(Path.Combine(profile, "fx49-expected-note"))))
            {
                return 92;
            }

            Console.Error.Write("fx49 fixture stderr");
            string refusal;
            switch (mode)
            {
                case "platform-refusal":
                    refusal = "PLATFORM_DENIED";
                    break;
                case "tool-refusal":
                    refusal = "TOOL_DENIED";
                    break;
                case "owner-refusal":
                    refusal = "OWNER_REPLY_REQUIRED";
                    break;
                default:
                    Console.Write("{\"success\":true,\"platform\":\"weixin\",\"chat_id\":" +
                        Quote("weixin:" + Id) + ",\"message_id\":" + Quote(Message) + "}");
                    return 0;
            }

            Console.Write("{\"success\":false,\"error_code\":" + Quote(refusal) + "}");
            return 7;
        }
        catch
        {
            return 93;
        }
    }

    private static string Target(string id, string type)
    {
        return "{\"id\":" + Quote(id) + ",\"type\":" + Quote(type) + "}";
    }

    private static string Quote(string value)
    {
        return "\"" + value.Replace("\\", "\\\\").Replace("\"", "\\\"") + "\"";
    }

    private static bool Matches<T>(T[] found, T[] expected)
    {
        if (found.Length != expected.Length)
        {
            return false;
        }
        for (int index = 0; index < expected.Length; index++)
        {
            if (!Object.Equals(found[index], expected[index]))
            {
                return false;
            }
        }
        return true;
    }
}
