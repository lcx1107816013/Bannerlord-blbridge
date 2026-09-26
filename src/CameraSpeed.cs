using System;
using System.Globalization;
using System.Reflection;
using TaleWorlds.Library;
using TaleWorlds.MountAndBlade.View.Screens;
using TaleWorlds.ScreenSystem;

namespace BlBridge
{
    /// <summary>
    /// 相机移动速度（v0.8.17）。用户 2026-09-25 反馈："原版相机移动速度太慢了"。
    ///
    /// 这**不是**一个万能开关，而是三条腿，因为速度是两套互不相干的公式：
    ///
    /// 【腿 1】engineShift —— 引擎自由相机的 Shift 倍率，走**官方控制台函数**，零反射。
    ///   依据（游戏反编译，行号为 decompiled 行号）：
    ///     `MissionScreen.cs:797` `[CommandLineArgumentFunction("set_shift_camera_speed","mission")]`
    ///       → 直接写私有字段 `_shiftSpeedMultiplier`（`:133` 默认 **3**）；
    ///     `:1749` `if (Input.IsGameKeyDown(24)) num6 *= (float)_shiftSpeedMultiplier;`
    ///       —— 这一句**不在** `if (Game.Current.CheatMode)` 块内（`:1714`），
    ///       所以改完**不需要开作弊模式**，按住 Shift 立刻生效；
    ///     调用入口 `CommandLineFunctionality.CallFunction(name, args, out found)`
    ///       是 `TaleWorlds.Library` 的 **public static**（零反射、零 Harmony）。
    ///   ⚠️ `AllFunctions` 由 `CollectCommandLineFunctions()` 填充（引擎把它当 `[EngineCallback]`
    ///      由 native 驱动），所以我们要**先自己收集一遍**，否则 found=false。
    ///
    /// 【腿 2】engineBase —— 引擎自由相机的基础倍率 `MissionScreen._cameraSpeedMultiplier`，
    ///   基础速度 = `10f * _cameraSpeedMultiplier * (…)`（`:1741`，默认 1）。
    ///   改它只能反射（它的热键 Ctrl+↑/↓ 在 `:1714` 的 `Game.Current.CheatMode` 门控里，
    ///   但**字段读写本身不在门控里**）。
    ///   ⚠️ 硬上限警告：`_cameraSpeed` 的速度分量每帧被 `MBMath.ClampFloat(…, -20f, 20f)`（`:1710-1712`）
    ///      夹住 ⇒ 倍率加到某个量之后**实际位移可能不再变快**。到底卡在哪一点，需要一次真机对照
    ///      （见 PROGRESS §二十九 的待办），别把"倍率调大了"当成"真的更快了"。
    ///
    /// 【腿 3】rtsCamera —— 装了 RTSCamera 时，改它的 `ICameraController.MovementSpeedFactor`。
    ///   这条最有效，因为**它的 clamp 随基础速度一起缩放**（没有上面那个固定 ±20 的天花板）。
    ///   依据（上游真源码 `lzh-mb-mod/RTSCamera` @ v5.4.16，MIT，本地只读克隆）：
    ///     `source/RTSCamera/src/View/FlyCameraMissionView.cs:506`
    ///       `float cameraBasicSpeed = 3f * _cameraSpeedMultiplier * MovementSpeedFactor;`
    ///     `:577-580` `float horizontalLimit = heightFactorForHorizontalMove * cameraBasicSpeed; …ClampFloat(±horizontalLimit)`
    ///     `:140-144` `MovementSpeedFactor { get => CameraSpeedFactor; set => CameraSpeedFactor = value; }`（默认 1）
    ///     `MissionLibrary/src/Controller/Camera/ICameraController.cs:11` `float MovementSpeedFactor { get; set; }`
    ///   取实例的官方路径是 `ACameraControllerManager.Get().Instance`（`ACameraControllerManager.cs:7/12`，
    ///   RTSCamera 自己的 `FlyCameraMissionView` 构造时把自己登记进去）。
    ///   这里**用反射按类型名去找**，不引用 RTSCamera 的程序集 —— 这样"没装 RTSCamera"时
    ///   本模块依然能正常加载（缺失时如实报 `leg_unavailable`，不静默）。
    ///
    /// 三条腿都遵守本项目的两条纪律：① 失败要**点名**（哪条腿、为什么、怎么办），不静默 no-op；
    /// ② 写完**回读**，回读值才是结论。
    /// </summary>
    internal static class CameraSpeed
    {
        private const string ShiftCliName = "mission.set_shift_camera_speed";
        private const string BaseFieldName = "_cameraSpeedMultiplier";
        private const string RtsManagerTypeName = "MissionLibrary.Controller.Camera.ACameraControllerManager";
        private const double MinValue = 0.1;
        private const double MaxValue = 1000.0;

        private static bool _cliCollected;

        // ── 测速探针（v0.8.18）：两次调用夹住一段"人在按 W"的时间 ──
        // 存在理由（2026-09-25 真机）：`base` / `shift` 到底有没有让相机变快、引擎那个 ±20 clamp
        // 会不会把效果吃掉，**光看回读值是答不了的**（回读只证明"值写进去了"）。
        // 而引擎自带的"摄像机移动速度"读数只在**观察者锁定**相机下才出现（装了 RTSCamera 时它还会
        // 在自由视角下把 spectator HUD 藏掉），我们这场 AI 对战根本拿不到 ⇒ 只能自己量：
        //   命令泵每 250 ms 跑一次（主线程），所以 `probe` 两次调用之间的位移 / 时间 = 真实平均速度。
        // 用法：`probe`（记起点）→ 人按住 W（或 Shift+W）飞一段 → `probe` + `end`（记终点、算速度）。
        private static DateTime _probeStartUtc;
        private static Vec3 _probeStartPos;
        private static string _probeStartValue;

        /// <summary>请求格式：`{"mode":"status|shift|base|rts|boost|probe","value":<数>,"action":"end"}`。</summary>
        internal static string HandleCommand(string raw)
        {
            try
            {
                string mode = Jmini.Str(raw, "mode", "");
                if (string.IsNullOrEmpty(mode)) mode = "status";

                if (mode == "status") return StatusJson();
                if (mode == "probe") return Probe(raw);
                if (mode == "shift" || mode == "base" || mode == "rts" || mode == "boost")
                    return Set(mode, raw);

                return Fail("bad_mode", "mode 只接受 status / shift / base / rts / boost / probe，收到 '" + mode + "'");
            }
            catch (Exception ex)
            {
                return Fail("handler_exception", ex.GetType().Name + ": " + ex.Message);
            }
        }

        // ───────────────────────── 测速探针 ─────────────────────────

        private static bool CameraPos(out Vec3 pos, out string why)
        {
            pos = Vec3.Zero;
            why = null;
            MissionScreen ms = Screen();
            if (ms == null)
            {
                why = "当前不在 mission 里";
                return false;
            }
            try
            {
                pos = ms.CombatCamera.Frame.origin;
                return true;
            }
            catch (Exception ex)
            {
                why = "读相机位置失败：" + ex.GetType().Name + ": " + ex.Message;
                return false;
            }
        }

        private static string Probe(string raw)
        {
            bool isEnd = string.Equals(Jmini.Str(raw, "action", ""), "end", StringComparison.OrdinalIgnoreCase);

            Vec3 pos;
            string why;
            if (!CameraPos(out pos, out why)) return Fail("not_in_mission", why);

            if (!isEnd)
            {
                _probeStartUtc = DateTime.UtcNow;
                _probeStartPos = pos;
                _probeStartValue = "x=" + Jw.N(pos.x) + ",y=" + Jw.N(pos.y) + ",z=" + Jw.N(pos.z);
                return "{\"ok\":true,\"phase\":\"start\",\"startPos\":{\"x\":" + Jw.N(pos.x)
                       + ",\"y\":" + Jw.N(pos.y) + ",\"z\":" + Jw.N(pos.z) + "}"
                       + ",\"note\":" + Protocol.Q("现在按住 W（或 Shift+W）飞一段，然后调 probe action=end") + "}";
            }

            if (_probeStartUtc == default(DateTime))
                return Fail("no_probe_start", "还没有 probe start（先不带 action 调一次）");

            double sec = (DateTime.UtcNow - _probeStartUtc).TotalSeconds;
            Vec3 d = pos - _probeStartPos;
            double dist = d.Length;
            double speed = sec > 0.001 ? dist / sec : 0.0;

            _probeStartUtc = default(DateTime);

            return "{\"ok\":true,\"phase\":\"end\""
                   + ",\"seconds\":" + Jw.N((float)sec)
                   + ",\"distance\":" + Jw.N((float)dist)
                   + ",\"speed\":" + Jw.N((float)speed)
                   + ",\"startPos\":" + Protocol.Q(_probeStartValue)
                   + ",\"endPos\":" + Protocol.Q("x=" + Jw.N(pos.x) + ",y=" + Jw.N(pos.y) + ",z=" + Jw.N(pos.z))
                   + ",\"note\":" + Protocol.Q("speed = 位移 / 墙钟秒数（命令泵 250ms 采样一次，够量平均速度）；"
                                               + "对比时请保持'按住同一个键、按同样久'") + "}";
        }

        // ───────────────────────── status ─────────────────────────

        internal static string StatusJson()
        {
            MissionScreen ms = Screen();
            bool inMission = ms != null;

            string shift, baseLeg, rts;

            // 腿 1：引擎 Shift 倍率（可读回 —— 无参调用会返回 "Current multiplier is N"）
            {
                string why;
                double v;
                bool ok = CliReadShift(out v, out why);
                shift = "{\"leg\":\"engineShift\",\"available\":" + Jw.B(ok)
                        + ",\"value\":" + (ok ? Jw.N((float)v) : "null")
                        + (ok ? "" : ",\"why\":" + Protocol.Q(why))
                        + ",\"how\":" + Protocol.Q("CommandLineFunctionality(" + ShiftCliName + ")")
                        + ",\"needsCheatMode\":" + Jw.B(false) + "}";
            }

            // 腿 2：引擎基础倍率（反射读）
            {
                string why;
                double v;
                bool ok = BaseRead(ms, out v, out why);
                baseLeg = "{\"leg\":\"engineBase\",\"available\":" + Jw.B(ok)
                          + ",\"value\":" + (ok ? Jw.N((float)v) : "null")
                          + (ok ? "" : ",\"why\":" + Protocol.Q(why))
                          + ",\"how\":" + Protocol.Q("reflection MissionScreen." + BaseFieldName)
                          + ",\"needsCheatMode\":" + Jw.B(false)
                          + ",\"caveat\":" + Protocol.Q("引擎相机的速度分量每帧被 clamp 在 ±20（MissionScreen.cs:1710-1712）"
                                                        + "⇒ 倍率调到某个量之后实际位移可能不再变快；"
                                                        + "装 RTSCamera 时优先用 rts 那条腿（它的 clamp 随基础速度缩放，没有这个天花板）")
                          + "}";
            }

            // 腿 3：RTSCamera 的 MovementSpeedFactor（反射读）
            {
                string why;
                double v;
                bool ok = RtsRead(out v, out why);
                rts = "{\"leg\":\"rtsCamera\",\"available\":" + Jw.B(ok)
                      + ",\"value\":" + (ok ? Jw.N((float)v) : "null")
                      + (ok ? "" : ",\"why\":" + Protocol.Q(why))
                      + ",\"how\":" + Protocol.Q("reflection " + RtsManagerTypeName + ".Get().Instance.MovementSpeedFactor")
                      + ",\"needsCheatMode\":" + Jw.B(false) + "}";
            }

            return "{\"ok\":true,\"inMission\":" + Jw.B(inMission)
                   + ",\"legs\":{\"engineShift\":" + shift
                   + ",\"engineBase\":" + baseLeg
                   + ",\"rtsCamera\":" + rts + "}}";
        }

        // ───────────────────────── set ─────────────────────────

        private static string Set(string mode, string raw)
        {
            if (!Jmini.Has(raw, "value"))
                return Fail("bad_value", "mode=" + mode + " 需要 value（数字）");

            double value = Jmini.Num(raw, "value", double.NaN);
            if (double.IsNaN(value) || double.IsInfinity(value))
                return Fail("bad_value", "value 不是数字");
            if (value < MinValue || value > MaxValue)
                return Fail("bad_value", "value 必须在 " + Jw.N((float)MinValue) + " ~ " + Jw.N((float)MaxValue)
                                        + " 之间，收到 " + Jw.N((float)value));

            if (mode == "boost") return Boost(value);

            string why;
            double readBack;
            bool ok;
            string code = null;
            string leg = mode;

            if (mode == "shift")
            {
                int iv = (int)Math.Round(value);
                if (iv < 1) return Fail("bad_value", "shift 的 value 必须是 ≥1 的整数（引擎控制台函数只接受 int）");
                ok = CliSetShift(iv, out why);
                if (ok) ok = CliReadShift(out readBack, out why);
                else readBack = double.NaN;
            }
            else if (mode == "base")
            {
                ok = BaseSet(Screen(), value, out why);
                if (ok) ok = BaseRead(Screen(), out readBack, out why);
                else readBack = double.NaN;
            }
            else // rts
            {
                ok = RtsSet(value, out why);
                if (ok) ok = RtsRead(out readBack, out why);
                else readBack = double.NaN;
            }

            if (!ok && why != null && why.IndexOf("No Mission Available", StringComparison.OrdinalIgnoreCase) >= 0)
                code = "not_in_mission";
            if (!ok && code == null)
                code = (mode == "rts") ? "leg_unavailable" : "set_failed";

            if (!ok)
                return "{\"ok\":false,\"code\":" + Protocol.Q(code)
                       + ",\"leg\":" + Protocol.Q(leg)
                       + ",\"requested\":" + Jw.N((float)value)
                       + ",\"error\":" + Protocol.Q(why ?? "未指明原因")
                       + "}";

            return "{\"ok\":true,\"leg\":" + Protocol.Q(leg)
                   + ",\"requested\":" + Jw.N((float)value)
                   + ",\"readBack\":" + Jw.N((float)readBack)
                   + ",\"changed\":" + Jw.B(Math.Abs(readBack - value) < 0.0005)
                   + ",\"note\":" + Protocol.Q(NoteFor(mode)) + "}";
        }

        /// <summary>把所有可用腿一次设成同一个值，并逐条报告哪条成功、哪条为什么不行。</summary>
        private static string Boost(double value)
        {
            string[] legs = new string[] { "shift", "base", "rts" };
            string parts = "";
            int success = 0;
            for (int i = 0; i < legs.Length; i++)
            {
                string leg = legs[i];
                string one = Set(leg, "{\"value\":" + Jw.N((float)value) + "}");
                if (one.IndexOf("\"ok\":true", StringComparison.Ordinal) >= 0) success++;
                if (parts.Length > 0) parts += ",";
                parts += "{\"leg\":" + Protocol.Q(leg) + ",\"result\":" + one + "}";
            }
            return "{\"ok\":" + Jw.B(success > 0) + ",\"mode\":\"boost\",\"value\":" + Jw.N((float)value)
                   + ",\"succeeded\":" + Jw.N(success) + ",\"legs\":[" + parts + "]}";
        }

        private static string NoteFor(string mode)
        {
            if (mode == "shift")
                return "引擎自由相机的 Shift 倍率（默认 3）。按住 Shift 飞即可，**不需要作弊模式**；"
                       + "数值只在本次游戏进程内有效（引擎不落盘）。";
            if (mode == "base")
                return "引擎自由相机的基础倍率（默认 1，基础速度 = 10 m/s × 它）。"
                       + "⚠️ 引擎把速度分量 clamp 在 ±20，调到某个量之后可能不再变快 —— 要结论请做一次真机对照。";
            return "RTSCamera 的相机速度系数（默认 1）。它的速度与上限都会随这个值一起放大，"
                   + "是目前最能把'慢'解决掉的一条腿（只在本场 mission 内有效，RTSCamera 退出时会清空）。";
        }

        // ───────────────────── 腿 1：官方控制台函数 ─────────────────────

        private static void EnsureCliCollected()
        {
            if (_cliCollected) return;
            try
            {
                // v0.8.20 加固（学自 BUTR/Bannerlord.GABS 的 `core/list_commands` 注释）：
                // `CollectCommandLineFunctions()` **不清空**私有静态字典 `AllFunctions`，
                // ⇒ **二次调用会抛"重复键"异常**。而引擎 native 自己也会调它
                // （`ManagedExtensions.CollectCommandLineFunctions` 是个 `[EngineCallback]`），
                // 谁先谁后不确定 ⇒ 有数据就直接用，不重复收集。
                if (!HasCollectedCommands())
                {
                    CommandLineFunctionality.CollectCommandLineFunctions();
                }
                _cliCollected = true;
            }
            catch
            {
                // 收集失败不致命：下面 CallFunction 会返回 found=false，由调用方如实报告
            }
        }

        /// <summary>`AllFunctions` 里已经有东西了吗？（private static Dictionary）</summary>
        private static bool HasCollectedCommands()
        {
            try
            {
                FieldInfo f = typeof(CommandLineFunctionality).GetField("AllFunctions",
                    BindingFlags.NonPublic | BindingFlags.Static);
                if (f == null) return false;
                System.Collections.IDictionary dict = f.GetValue(null) as System.Collections.IDictionary;
                return dict != null && dict.Count > 0;
            }
            catch
            {
                return false;
            }
        }

        private static bool CliSetShift(int value, out string why)
        {
            why = null;
            try
            {
                EnsureCliCollected();
                bool found;
                string r = CommandLineFunctionality.CallFunction(
                    ShiftCliName, value.ToString(CultureInfo.InvariantCulture), out found);
                if (!found)
                {
                    why = "引擎里没有控制台函数 '" + ShiftCliName + "'（游戏版本可能变了）";
                    return false;
                }
                if (!string.Equals(r, "Done", StringComparison.Ordinal))
                {
                    // 官方实现：不在 mission 里返回 "No Mission Available"
                    why = "控制台函数返回：" + r;
                    return false;
                }
                return true;
            }
            catch (Exception ex)
            {
                why = "调用控制台函数抛异常：" + ex.GetType().Name + ": " + ex.Message;
                return false;
            }
        }

        private static bool CliReadShift(out double value, out string why)
        {
            value = double.NaN;
            why = null;
            try
            {
                EnsureCliCollected();
                bool found;
                string r = CommandLineFunctionality.CallFunction(ShiftCliName, "", out found);
                if (!found)
                {
                    why = "引擎里没有控制台函数 '" + ShiftCliName + "'（游戏版本可能变了）";
                    return false;
                }
                // 无参调用的官方返回："Current multiplier is 3" / "No Mission Available"
                // v0.8.18：先认引擎那句 "No Mission Available" —— 早先它被当成"读回值无法解析"报出去，
                // 读的人会以为是自己解析出 bug（2026-09-25 真机负对照暴露）。
                if (r.IndexOf("No Mission Available", StringComparison.OrdinalIgnoreCase) >= 0)
                {
                    why = "当前不在 mission 里（引擎原话：No Mission Available）";
                    return false;
                }
                int sp = r.LastIndexOf(' ');
                double v;
                if (sp >= 0 && double.TryParse(r.Substring(sp + 1), NumberStyles.Float,
                                              CultureInfo.InvariantCulture, out v))
                {
                    value = v;
                    return true;
                }
                why = "读回值无法解析：" + r;
                return false;
            }
            catch (Exception ex)
            {
                why = "调用控制台函数抛异常：" + ex.GetType().Name + ": " + ex.Message;
                return false;
            }
        }

        // ───────────────────── 腿 2：引擎基础倍率（反射） ─────────────────────

        private static FieldInfo BaseField()
        {
            return typeof(MissionScreen).GetField(BaseFieldName,
                BindingFlags.Instance | BindingFlags.NonPublic | BindingFlags.Public);
        }

        private static bool BaseRead(MissionScreen ms, out double value, out string why)
        {
            value = double.NaN;
            why = null;
            if (ms == null)
            {
                why = "当前不在 mission 里（ScreenManager.TopScreen 不是 MissionScreen）";
                return false;
            }
            FieldInfo f = BaseField();
            if (f == null)
            {
                why = "反射找不到 MissionScreen." + BaseFieldName + "（游戏版本可能改了字段名）";
                return false;
            }
            try
            {
                value = Convert.ToDouble(f.GetValue(ms), CultureInfo.InvariantCulture);
                return true;
            }
            catch (Exception ex)
            {
                why = "读字段失败：" + ex.GetType().Name + ": " + ex.Message;
                return false;
            }
        }

        private static bool BaseSet(MissionScreen ms, double value, out string why)
        {
            why = null;
            if (ms == null)
            {
                why = "当前不在 mission 里；引擎这条腿只能在战斗/部署界面里改";
                return false;
            }
            FieldInfo f = BaseField();
            if (f == null)
            {
                why = "反射找不到 MissionScreen." + BaseFieldName + "（游戏版本可能改了字段名）";
                return false;
            }
            try
            {
                f.SetValue(ms, (float)value);
                return true;
            }
            catch (Exception ex)
            {
                why = "写字段失败：" + ex.GetType().Name + ": " + ex.Message;
                return false;
            }
        }

        // ───────────────────── 腿 3：RTSCamera 的相机控制器 ─────────────────────

        private static Type FindType(string fullName)
        {
            Assembly[] asms = AppDomain.CurrentDomain.GetAssemblies();
            for (int i = 0; i < asms.Length; i++)
            {
                try
                {
                    Type t = asms[i].GetType(fullName, false);
                    if (t != null) return t;
                }
                catch
                {
                    // 个别程序集 GetType 会抛（动态程序集等），跳过即可
                }
            }
            return null;
        }

        /// <summary>拿到 RTSCamera 的相机控制器实例；拿不到时 why 说明卡在哪一步（不静默）。</summary>
        private static object RtsController(out string why)
        {
            why = null;
            Type mgrType = FindType(RtsManagerTypeName);
            if (mgrType == null)
            {
                why = "没找到类型 " + RtsManagerTypeName + "（本机应该没装 RTSCamera，或它的共享库没加载）";
                return null;
            }
            try
            {
                MethodInfo get = mgrType.GetMethod("Get", BindingFlags.Public | BindingFlags.Static);
                if (get == null)
                {
                    why = mgrType.FullName + ".Get() 不存在（RTSCamera 版本变了）";
                    return null;
                }
                object mgr = get.Invoke(null, null);
                if (mgr == null)
                {
                    why = mgrType.FullName + ".Get() 返回 null（RTSCamera 的共享库未初始化）";
                    return null;
                }
                PropertyInfo instProp = mgrType.GetProperty("Instance", BindingFlags.Public | BindingFlags.Instance);
                if (instProp == null)
                {
                    why = mgrType.FullName + ".Instance 不存在（RTSCamera 版本变了）";
                    return null;
                }
                object inst = instProp.GetValue(mgr, null);
                if (inst == null)
                {
                    why = "相机控制器尚未注册：RTSCamera 只在 mission 内把自己登记进去"
                          + "（不在战斗里，或这一场它没接管相机）";
                    return null;
                }
                return inst;
            }
            catch (Exception ex)
            {
                why = "反射取相机控制器失败：" + ex.GetType().Name + ": " + ex.Message;
                return null;
            }
        }

        private static PropertyInfo RtsFactorProperty(object controller, out string why)
        {
            why = null;
            PropertyInfo p = controller.GetType().GetProperty("MovementSpeedFactor",
                BindingFlags.Public | BindingFlags.Instance);
            if (p == null)
            {
                why = "相机控制器 " + controller.GetType().FullName
                      + " 上没有 MovementSpeedFactor 属性（RTSCamera 版本变了）";
            }
            return p;
        }

        private static bool RtsRead(out double value, out string why)
        {
            value = double.NaN;
            object inst = RtsController(out why);
            if (inst == null) return false;
            PropertyInfo p = RtsFactorProperty(inst, out why);
            if (p == null) return false;
            try
            {
                value = Convert.ToDouble(p.GetValue(inst, null), CultureInfo.InvariantCulture);
                return true;
            }
            catch (Exception ex)
            {
                why = "读 MovementSpeedFactor 失败：" + ex.GetType().Name + ": " + ex.Message;
                return false;
            }
        }

        private static bool RtsSet(double value, out string why)
        {
            object inst = RtsController(out why);
            if (inst == null) return false;
            PropertyInfo p = RtsFactorProperty(inst, out why);
            if (p == null) return false;
            try
            {
                p.SetValue(inst, (float)value, null);
                return true;
            }
            catch (Exception ex)
            {
                why = "写 MovementSpeedFactor 失败：" + ex.GetType().Name + ": " + ex.Message;
                return false;
            }
        }

        // ───────────────────────── 公共 ─────────────────────────

        private static MissionScreen Screen()
        {
            try
            {
                return ScreenManager.TopScreen as MissionScreen;
            }
            catch
            {
                return null;
            }
        }

        private static string Fail(string code, string message)
        {
            return "{\"ok\":false,\"code\":" + Protocol.Q(code) + ",\"error\":" + Protocol.Q(message) + "}";
        }
    }
}
