using System;
using System.Globalization;
using System.Text;

namespace BlBridge
{
    /// <summary>
    /// 极简扁平 JSON 读取器。命令请求是我们自己（MCP 侧）生成的固定形状的对象，
    /// 所以不需要完整 JSON 解析器 —— 避免引入 Newtonsoft 带来运行时程序集解析风险。
    /// 支持：字符串（含 \" \\ \n \r \t 与 \uXXXX）、整数、浮点、布尔。
    ///
    /// v0.4.0 修正：键查找改为**词法扫描**，只认真正的键（字符串结束后紧跟 ':'），
    /// 不再匹配"字符串值里的同名文本"。旧实现用 IndexOf("\"key\"") 全文搜索，
    /// 会被 {"note":"\"method\":\"evil\""} 这类输入劫持（对照 Coop 用真实反序列化）。
    /// 嵌套键（如 parameters 内的 enabled）仍然能被找到，调用方行为不变。
    /// </summary>
    internal static class Jmini
    {
        /// <summary>找到键对应的**值起始位置**；找不到返回 -1。会跳过字符串字面量内部。</summary>
        private static int FindKey(string json, string key)
        {
            if (json == null || key == null || key.Length == 0) return -1;
            int i = 0;
            while (i < json.Length)
            {
                char c = json[i];
                if (c != '"')
                {
                    i++;
                    continue;
                }

                // 读一个字符串字面量（可能是键，也可能是值）
                int start = i + 1;
                int j = start;
                while (j < json.Length)
                {
                    char sj = json[j];
                    if (sj == '\\')
                    {
                        j += 2;      // 跳过被转义的字符（含 \" 与 \\）
                        continue;
                    }
                    if (sj == '"') break;
                    j++;
                }

                int end = Math.Min(j, json.Length);
                int k = end + 1;
                while (k < json.Length && char.IsWhiteSpace(json[k])) k++;
                bool looksLikeKey = k < json.Length && json[k] == ':';

                if (looksLikeKey && string.Equals(json.Substring(start, end - start), key, StringComparison.Ordinal))
                {
                    k++;                                                    // 跳过 ':'
                    while (k < json.Length && char.IsWhiteSpace(json[k])) k++;
                    return k;                                               // 值起始位置
                }

                i = (j < json.Length) ? j + 1 : json.Length;                 // 越过这个字符串字面量
            }
            return -1;
        }

        public static bool Has(string json, string key)
        {
            return FindKey(json, key) >= 0;
        }

        public static string Str(string json, string key, string fallback)
        {
            int p = FindKey(json, key);
            if (p < 0 || p >= json.Length || json[p] != '"') return fallback;
            p++;
            StringBuilder sb = new StringBuilder();
            while (p < json.Length)
            {
                char c = json[p];
                if (c == '\\' && p + 1 < json.Length)
                {
                    char n = json[p + 1];
                    p += 2;
                    switch (n)
                    {
                        case 'n': sb.Append('\n'); break;
                        case 'r': sb.Append('\r'); break;
                        case 't': sb.Append('\t'); break;
                        case 'b': sb.Append('\b'); break;
                        case 'f': sb.Append('\f'); break;
                        case '"': sb.Append('"'); break;
                        case '\\': sb.Append('\\'); break;
                        case '/': sb.Append('/'); break;
                        case 'u':
                            int code;
                            if (ReadHex4(json, p, out code))
                            {
                                // 合法代理对：两半各自 Append 后仍是正确的一对；
                                // 孤立代理项会产出非法 UTF-16，统一降级为 U+FFFD。
                                if (code >= 0xD800 && code <= 0xDBFF)
                                {
                                    int low;
                                    // p 指向第一个转义的 4 位十六进制；第二个转义的十六进制在 p+6
                                    //（跳过 4 位数字 + 下一个转义的 "\u"）
                                    if (ReadHex4(json, p + 6, out low) && low >= 0xDC00 && low <= 0xDFFF)
                                    {
                                        sb.Append((char)code).Append((char)low);
                                        // 连同第二个转义的 "\u" 一起跳过（外层循环末尾还会再 +4）
                                        p += 6;
                                    }
                                    else
                                    {
                                        sb.Append('\uFFFD');
                                    }
                                }
                                else if (code >= 0xDC00 && code <= 0xDFFF)
                                {
                                    sb.Append('\uFFFD');
                                }
                                else
                                {
                                    sb.Append((char)code);
                                }
                                p += 4;
                            }
                            break;
                        default: sb.Append(n); break;
                    }
                    continue;
                }
                if (c == '"') break;
                sb.Append(c);
                p++;
            }
            return sb.ToString();
        }

        /// <summary>从 json[pos..pos+4) 读 4 位十六进制；越界或不合法返回 false。</summary>
        private static bool ReadHex4(string json, int pos, out int value)
        {
            value = 0;
            if (json == null || pos < 0 || pos + 4 > json.Length) return false;
            return int.TryParse(json.Substring(pos, 4), NumberStyles.HexNumber,
                CultureInfo.InvariantCulture, out value);
        }

        public static double Num(string json, string key, double fallback)
        {
            int p = FindKey(json, key);
            if (p < 0) return fallback;
            int start = p;
            while (p < json.Length && (char.IsDigit(json[p]) || json[p] == '-' || json[p] == '+' ||
                                       json[p] == '.' || json[p] == 'e' || json[p] == 'E'))
            {
                p++;
            }
            if (p == start) return fallback;
            double v;
            if (double.TryParse(json.Substring(start, p - start), NumberStyles.Float,
                    CultureInfo.InvariantCulture, out v))
            {
                return v;
            }
            return fallback;
        }

        public static int Int(string json, string key, int fallback)
        {
            double v = Num(json, key, fallback);
            return (int)Math.Round(v);
        }

        public static bool Bool(string json, string key, bool fallback)
        {
            int p = FindKey(json, key);
            if (p < 0) return fallback;
            if (string.Compare(json, p, "true", 0, 4, StringComparison.OrdinalIgnoreCase) == 0) return true;
            if (string.Compare(json, p, "false", 0, 5, StringComparison.OrdinalIgnoreCase) == 0) return false;
            return fallback;
        }
    }
}
