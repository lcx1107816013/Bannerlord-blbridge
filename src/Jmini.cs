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

        /// <summary>
        /// v0.8.46：把"键对应的**值起始位置**"暴露出来（`FindKey` 本来就是干这个的）。
        /// 用途：需要读**嵌套对象内部**的键时，先切出对象文本（见 `Protocol.ExtractObject`），
        /// 再在子串里扁平查找 —— `Jmini` 刻意不做嵌套解析，这是最小可行的补法。
        /// 找不到 ⇒ -1。
        /// </summary>
        internal static int ValueStart(string json, string key)
        {
            return FindKey(json, key);
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
            // 形态 ①：裸 JSON 字面量 `true` / `false`（MCP 侧发的是这种）
            if (string.Compare(json, p, "true", 0, 4, StringComparison.OrdinalIgnoreCase) == 0) return true;
            if (string.Compare(json, p, "false", 0, 5, StringComparison.OrdinalIgnoreCase) == 0) return false;
            // 形态 ②：带引号的字符串 `"true"` / `"false"`（CLI 侧的既有约定，见 README：
            //   "布尔参数一律发字符串"）。两种都收 —— 只认一种就是"调用方猜谜"。
            //
            // 2026-09-25 真机踩到（v0.8.14 验证轮）：CLI 发 `"spectate": "true"`、
            // 旧实现只认裸字面量 ⇒ 返回值落到 fallback=false，**参数被静默丢弃**：
            // 请求 accepted、响应里 spectate=false，既不报错也不生效。
            // 这类"没报错但没生效"比崩溃更难查，所以在此一并收口，而不是去改调用方。
            string s = Str(json, key, null);
            if (s == null) return fallback;
            if (string.Equals(s, "true", StringComparison.OrdinalIgnoreCase)) return true;
            if (string.Equals(s, "false", StringComparison.OrdinalIgnoreCase)) return false;
            return fallback;
        }

        /// <summary>
        /// **三态**布尔读取：`true` / `false` / **`null`（键存在但值不是布尔，或值就是 JSON null）**。
        ///
        /// v0.8.46 新增，动机（账本方案 A）：`Bool(json,key,fallback)` 把"缺席"和"值为 null"
        /// **压成同一个返回值**，于是"这个结果是失败"与"这个结果没有 ok 字段"无法区分。
        /// 账本新增的 `resultOk` 字段必须能表达三态（成功/失败/**未知**）——
        /// 历史行没有这个键 ⇒ 必须是"未知"，**不能**假装它成功或失败。
        ///
        /// 判据：`Has(json,key)==false` ⇒ 返回 **null**（缺席）；键存在时按 `Bool` 的两种形态解析，
        /// 仍然解不出布尔（含字面量 `null`）⇒ 也返回 **null**。
        /// ⚠️ 与 `Bool(..., fallback)` 的分工：**需要 fallback 的老调用点一律不动**，
        /// 只有确实要区分三态的新调用点才用它。
        /// </summary>
        public static bool? BoolOrNull(string json, string key)
        {
            if (!Has(json, key)) return null;              // 键缺席
            int p = FindKey(json, key);
            if (p < 0 || p >= json.Length) return null;
            char c = json[p];
            if (c == 'n' || c == 'N')
            {
                // JSON 字面量 null（后面可能跟逗号/花括号，只要前 4 字符是 null 即可）
                if (string.Compare(json, p, "null", 0, 4, StringComparison.OrdinalIgnoreCase) == 0)
                    return null;
            }
            if (string.Compare(json, p, "true", 0, 4, StringComparison.OrdinalIgnoreCase) == 0) return true;
            if (string.Compare(json, p, "false", 0, 5, StringComparison.OrdinalIgnoreCase) == 0) return false;
            // 带引号形态（CLI 约定）
            if (c == '"')
            {
                string s = Str(json, key, null);
                if (string.Equals(s, "true", StringComparison.OrdinalIgnoreCase)) return true;
                if (string.Equals(s, "false", StringComparison.OrdinalIgnoreCase)) return false;
            }
            return null;                                   // 存在但不是布尔 ⇒ 未知
        }
    }
}
