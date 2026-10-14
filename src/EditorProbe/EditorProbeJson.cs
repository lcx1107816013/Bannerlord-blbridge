using System;
using System.Collections.Generic;
using System.Globalization;
using System.Text;

namespace BlBridge.EditorProbe
{
    /// <summary>
    /// A small JSON reader for the editor probe's wire format.
    ///
    /// Why not the framework's serializer: this has to run on .NET Framework 4.8
    /// with no added dependencies (BlBridge's whole distribution promise is "drop
    /// the DLL in Modules and it works"), and the shapes crossing this boundary
    /// are narrow — one object, string/number/bool/null/array/object values. A
    /// purpose-built reader keeps the dependency surface at zero and its behaviour
    /// verifiable offline, which is exactly what the probe's tests need.
    ///
    /// It deliberately REJECTS rather than repairs: anything it cannot parse throws,
    /// and the caller answers `invalid_request`. A lenient reader that guessed at
    /// malformed input would turn a client bug into a mystery.
    /// </summary>
    internal static class EditorProbeJson
    {
        internal static Dictionary<string, object> ParseObject(string text)
        {
            if (text == null) throw new FormatException("null input");
            int index = 0;
            SkipWhitespace(text, ref index);
            if (index >= text.Length || text[index] != '{')
                throw new FormatException("expected an object");
            object value = ParseValue(text, ref index);
            SkipWhitespace(text, ref index);
            if (index != text.Length)
                throw new FormatException("trailing content after the object");
            return value as Dictionary<string, object>;
        }

        private static object ParseValue(string s, ref int i)
        {
            SkipWhitespace(s, ref i);
            if (i >= s.Length) throw new FormatException("unexpected end of input");

            char c = s[i];
            switch (c)
            {
                case '{': return ParseObjectBody(s, ref i);
                case '[': return ParseArray(s, ref i);
                case '"': return ParseString(s, ref i);
                case 't': Expect(s, ref i, "true"); return true;
                case 'f': Expect(s, ref i, "false"); return false;
                case 'n': Expect(s, ref i, "null"); return null;
                default: return ParseNumber(s, ref i);
            }
        }

        private static Dictionary<string, object> ParseObjectBody(string s, ref int i)
        {
            Dictionary<string, object> result = new Dictionary<string, object>(StringComparer.Ordinal);
            i++; // '{'
            SkipWhitespace(s, ref i);
            if (i < s.Length && s[i] == '}') { i++; return result; }

            while (true)
            {
                SkipWhitespace(s, ref i);
                if (i >= s.Length || s[i] != '"') throw new FormatException("expected a key");
                string key = ParseString(s, ref i);
                SkipWhitespace(s, ref i);
                if (i >= s.Length || s[i] != ':') throw new FormatException("expected ':'");
                i++;
                object value = ParseValue(s, ref i);
                // Last one wins, matching JSON object semantics in every mainstream
                // parser. Silently keeping the first would diverge from the client.
                result[key] = value;
                SkipWhitespace(s, ref i);
                if (i >= s.Length) throw new FormatException("unterminated object");
                if (s[i] == ',') { i++; continue; }
                if (s[i] == '}') { i++; return result; }
                throw new FormatException("expected ',' or '}'");
            }
        }

        private static List<object> ParseArray(string s, ref int i)
        {
            List<object> result = new List<object>();
            i++; // '['
            SkipWhitespace(s, ref i);
            if (i < s.Length && s[i] == ']') { i++; return result; }

            while (true)
            {
                result.Add(ParseValue(s, ref i));
                SkipWhitespace(s, ref i);
                if (i >= s.Length) throw new FormatException("unterminated array");
                if (s[i] == ',') { i++; continue; }
                if (s[i] == ']') { i++; return result; }
                throw new FormatException("expected ',' or ']'");
            }
        }

        private static string ParseString(string s, ref int i)
        {
            i++; // opening quote
            StringBuilder sb = new StringBuilder();
            while (true)
            {
                if (i >= s.Length) throw new FormatException("unterminated string");
                char c = s[i++];
                if (c == '"') return sb.ToString();
                if (c != '\\') { sb.Append(c); continue; }

                if (i >= s.Length) throw new FormatException("unterminated escape");
                char e = s[i++];
                switch (e)
                {
                    case '"': sb.Append('"'); break;
                    case '\\': sb.Append('\\'); break;
                    case '/': sb.Append('/'); break;
                    case 'b': sb.Append('\b'); break;
                    case 'f': sb.Append('\f'); break;
                    case 'n': sb.Append('\n'); break;
                    case 'r': sb.Append('\r'); break;
                    case 't': sb.Append('\t'); break;
                    case 'u':
                        if (i + 4 > s.Length) throw new FormatException("truncated \\u escape");
                        sb.Append((char)ushort.Parse(s.Substring(i, 4), NumberStyles.HexNumber, CultureInfo.InvariantCulture));
                        i += 4;
                        break;
                    default: throw new FormatException("unknown escape \\" + e);
                }
            }
        }

        private static object ParseNumber(string s, ref int i)
        {
            int start = i;
            while (i < s.Length && "+-.eE0123456789".IndexOf(s[i]) >= 0) i++;
            if (i == start) throw new FormatException("expected a value");
            string token = s.Substring(start, i - start);

            long l;
            if (!token.Contains(".") && !token.Contains("e") && !token.Contains("E")
                && long.TryParse(token, NumberStyles.Integer, CultureInfo.InvariantCulture, out l))
                return l;

            double d;
            if (double.TryParse(token, NumberStyles.Float, CultureInfo.InvariantCulture, out d))
                return d;

            throw new FormatException("bad number: " + token);
        }

        private static void Expect(string s, ref int i, string literal)
        {
            if (i + literal.Length > s.Length || s.Substring(i, literal.Length) != literal)
                throw new FormatException("expected " + literal);
            i += literal.Length;
        }

        private static void SkipWhitespace(string s, ref int i)
        {
            while (i < s.Length && (s[i] == ' ' || s[i] == '\t' || s[i] == '\n' || s[i] == '\r')) i++;
        }

        // ── typed accessors: absent and wrong-typed both read as "not provided" ──

        internal static object Get(Dictionary<string, object> o, string key)
        {
            if (o == null) return null;
            object v;
            return o.TryGetValue(key, out v) ? v : null;
        }

        internal static string GetString(Dictionary<string, object> o, string key)
        {
            object v = Get(o, key);
            return v as string;
        }

        internal static Dictionary<string, object> GetObject(Dictionary<string, object> o, string key)
        {
            return Get(o, key) as Dictionary<string, object>;
        }

        internal static List<object> GetArray(Dictionary<string, object> o, string key)
        {
            return Get(o, key) as List<object>;
        }

        internal static bool TryGetNumber(Dictionary<string, object> o, string key, out double value)
        {
            value = 0;
            object raw = Get(o, key);
            if (raw is long) { value = (long)raw; return true; }
            if (raw is double) { value = (double)raw; return true; }
            return false;
        }

        internal static bool TryGetInt(Dictionary<string, object> o, string key, out int value)
        {
            value = 0;
            double d;
            if (!TryGetNumber(o, key, out d)) return false;
            if (d < int.MinValue || d > int.MaxValue) return false;
            value = (int)Math.Round(d);
            return true;
        }

        internal static string Escape(string s)
        {
            if (string.IsNullOrEmpty(s)) return "";
            StringBuilder sb = new StringBuilder(s.Length + 8);
            for (int i = 0; i < s.Length; i++)
            {
                char c = s[i];
                switch (c)
                {
                    case '"': sb.Append("\\\""); break;
                    case '\\': sb.Append("\\\\"); break;
                    case '\b': sb.Append("\\b"); break;
                    case '\f': sb.Append("\\f"); break;
                    case '\n': sb.Append("\\n"); break;
                    case '\r': sb.Append("\\r"); break;
                    case '\t': sb.Append("\\t"); break;
                    default:
                        // Control characters must be escaped; anything else is passed
                        // through so non-ASCII paths (Chinese, Cyrillic) stay readable.
                        if (c < ' ') sb.Append("\\u").Append(((int)c).ToString("x4", CultureInfo.InvariantCulture));
                        else sb.Append(c);
                        break;
                }
            }
            return sb.ToString();
        }

        internal static string Quote(string s)
        {
            return "\"" + Escape(s ?? "") + "\"";
        }
    }
}
