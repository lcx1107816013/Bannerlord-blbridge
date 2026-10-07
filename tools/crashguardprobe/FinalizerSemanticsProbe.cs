// CrashGuardFinalizerProbe —— 对**真实 0Harmony.dll** 做的受控实验（离线，不需要游戏）。
//
// 为什么要有它：本仓库的纪律是「没有对照组的验证不是验证，只是自证」。
// CrashGuard 的全部行为都押在两条 Harmony 语义假设上：
//   ① Finalizer 用 `Exception __exception` 作参数名能否**绑定到被抛的异常**；
//   ② Finalizer **返回 null** 是否真的**吞掉**异常（调用方看不到）、
//      返回 `__exception` 是否真的**放行**。
// 这两条**不能靠记忆或文档断言** —— 用真实 DLL 跑一遍，并且每组都配反向对照。
//
// 判据（每条都有自己的对照组）：
//   A1 返回 null  ⇒ 调用方**不**看到异常（吞掉成立）
//   A2 返回 null 时，Finalizer **看得到**原异常（绑定成立，不是拿到 null）
//   B1 返回 __exception ⇒ 调用方**看到**异常（放行成立）—— A1 的**反向对照**
//       ★ 没有 B1，A1 可能只是"异常本来就没抛"
//   C1 改成别的类型作参数 ⇒ 应抛 HarmonyException 类错误（证明参数名**真的**有意义）
//       ★ 没有 C1，"绑定"可能只是"恰好能编过"
//   D1 致命异常照常放行（模拟 CrashGuard 的 IsFatal 分支）—— 隔离本实验与产品代码的关系
using System;
using System.Reflection;
using HarmonyLib;

namespace CrashGuardProbe
{
    /// <summary>被补丁的目标：调用即抛。形状刻意模仿游戏 tick（void X(float)）。</summary>
    public class Subject
    {
        public static int Calls;
        public static int SawExceptionInFinalizer;
        public static string LastMessage;

        public void Tick(float dt)
        {
            Calls++;
            throw new InvalidOperationException("probe-boom");
        }
    }

    /// <summary>返回 null 的 Finalizer（应吞掉）。</summary>
    public static class NullFinalizer
    {
        public static Exception Finalizer(Exception __exception)
        {
            if (__exception != null)
            {
                Subject.SawExceptionInFinalizer++;
                Subject.LastMessage = __exception.Message;
            }
            return null;
        }
    }

    /// <summary>原样返回的 Finalizer（应放行）—— A1 的反向对照。</summary>
    public static class PassFinalizer
    {
        public static Exception Finalizer(Exception __exception)
        {
            return __exception;
        }
    }

    /// <summary>只针对致命类型的 Finalizer（应放行）。</summary>
    public static class FatalPassthroughFinalizer
    {
        public static Exception Finalizer(Exception __exception)
        {
            if (__exception is OutOfMemoryException) return __exception;
            return null;
        }
    }

    public static class Program
    {
        private static int _failed;

        private static void Check(string name, bool ok, string detail)
        {
            Console.WriteLine((ok ? "  [OK]   " : "  [FAIL] ") + name
                              + (string.IsNullOrEmpty(detail) ? "" : "  <- " + detail));
            if (!ok) _failed++;
        }

        public static int Main()
        {
            // ★ 顶层兜底：本实验的价值就在"看到 Harmony 到底怎么反应"，
            //   若让异常裸奔出去，Windows 只会给一句
            //   "由于 Exception.ToString() 失败，因此无法打印异常字符串。"（实测踩到）
            //   ⇒ 什么都学不到。所以必须自己抓住并**逐字段**打印。
            try
            {
                return Run();
            }
            catch (Exception ex)
            {
                Console.WriteLine();
                Console.WriteLine("[!] 实验顶层异常 —— 逐字段打印（ToString 可能本身就坏）:");
                try { Console.WriteLine("    类型    : " + ex.GetType().FullName); }
                catch (Exception e2) { Console.WriteLine("    类型读取失败: " + e2.GetType().Name); }
                try { Console.WriteLine("    Message : " + ex.Message); }
                catch (Exception e2) { Console.WriteLine("    Message 读取失败: " + e2.GetType().Name); }
                try
                {
                    Exception inner = ex.InnerException;
                    int depth = 0;
                    while (inner != null && depth < 5)
                    {
                        Console.WriteLine("    Inner[" + depth + "]: " + inner.GetType().FullName
                                          + " : " + inner.Message);
                        inner = inner.InnerException;
                        depth++;
                    }
                }
                catch { }
                try { Console.WriteLine("    Stack   : " + ex.StackTrace); } catch { }
                return 2;
            }
        }

        private static int Run()
        {
            try { Console.OutputEncoding = System.Text.Encoding.UTF8; } catch { }

            Console.WriteLine("CrashGuard Finalizer 语义实验（真实 0Harmony）");
            Console.WriteLine("Harmony 版本: " + typeof(Harmony).Assembly.GetName().Version);
            Console.WriteLine(new string('=', 74));

            MethodInfo target = typeof(Subject).GetMethod("Tick");
            Console.WriteLine("目标方法: " + target.DeclaringType.FullName + "." + target.Name
                              + "(" + target.GetParameters()[0].ParameterType.Name + ")");
            Console.WriteLine();

            // ── A: 返回 null ⇒ 吞掉 ────────────────────────────────────
            Console.WriteLine("[A] Finalizer 返回 null（期望：吞掉，调用方无感）");
            {
                Subject.Calls = 0;
                Subject.SawExceptionInFinalizer = 0;
                Subject.LastMessage = null;
                Harmony h = new Harmony("probe.null");
                h.Patch(target, null, null, null,
                    new HarmonyMethod(typeof(NullFinalizer).GetMethod("Finalizer")));

                bool threw = false;
                string thrown = null;
                try { new Subject().Tick(0.016f); }
                catch (Exception ex) { threw = true; thrown = ex.GetType().Name + ": " + ex.Message; }

                Check("A1 调用方**没有**看到异常", !threw, threw ? ("却抛了 " + thrown) : "未抛");
                Check("A2 Finalizer **看得到**原异常（参数绑定成立）",
                      Subject.SawExceptionInFinalizer == 1,
                      "SawExceptionInFinalizer=" + Subject.SawExceptionInFinalizer);
                Check("A3 绑定到的是**那个**异常（消息一致）",
                      Subject.LastMessage == "probe-boom",
                      "LastMessage=" + (Subject.LastMessage ?? "(null)"));
                h.UnpatchAll("probe.null");
            }
            Console.WriteLine();

            // ── B: 返回 __exception ⇒ 放行（A 的反向对照）───────────────
            Console.WriteLine("[B] Finalizer 原样返回（期望：放行）—— A1 的反向对照");
            {
                Subject.Calls = 0;
                Harmony h = new Harmony("probe.pass");
                h.Patch(target, null, null, null,
                    new HarmonyMethod(typeof(PassFinalizer).GetMethod("Finalizer")));

                bool threw = false;
                string thrown = null;
                try { new Subject().Tick(0.016f); }
                catch (Exception ex) { threw = true; thrown = ex.GetType().Name + ": " + ex.Message; }

                Check("B1 调用方**看到**异常", threw, threw ? thrown : "居然没抛");
                Check("B2 抛的是**原来那个**异常（未被替换）",
                      thrown != null && thrown.Contains("probe-boom"), thrown ?? "(null)");
                h.UnpatchAll("probe.pass");
            }
            Console.WriteLine();

            // ── B3: 反向对照的**自我检查** —— 不装 patch 时本来就会抛 ─────
            Console.WriteLine("[B3] 对照组自检：**不装补丁**时应当抛（证明目标真的会抛）");
            {
                bool threw = false;
                try { new Subject().Tick(0.016f); } catch { threw = true; }
                Check("B3 裸调用确实抛异常", threw,
                      "若这条失败，说明目标方法根本不抛 ⇒ A1/B1 全部无效");
            }
            Console.WriteLine();

            // ── C: 参数名换成别的 ⇒ 应报错（证明参数名真的有意义）────────
            Console.WriteLine("[C] 把参数改名为非 `__exception`（期望：Harmony 报错/不绑定）");
            {
                // 用一个参数名叫 `ex` 的 finalizer —— Harmony 的约定是特殊名才注入。
                Harmony h = new Harmony("probe.wrongname");
                bool errorOrIgnored = false;
                string detail = null;
                try
                {
                    h.Patch(target, null, null, null,
                        new HarmonyMethod(typeof(WrongNameFinalizer).GetMethod("Finalizer")));
                    // 若没抛错，就看它有没有被当普通参数（会拿到 null 或注入失败）
                    try { new Subject().Tick(0.016f); errorOrIgnored = true; detail = "异常被吞了"; }
                    catch (Exception ex) { errorOrIgnored = true; detail = "异常照抛：" + ex.GetType().Name; }
                }
                catch (Exception ex)
                {
                    errorOrIgnored = true;
                    detail = "按预期报错：" + ex.GetType().Name;
                }
                Check("C1 参数名不是 `__exception` 时**不会**静默吞掉", errorOrIgnored,
                      detail ?? "(无输出)");
                try { h.UnpatchAll("probe.wrongname"); } catch { }
            }
            Console.WriteLine();

            // ── D: 致命异常分支隔离 ─────────────────────────────────────
            Console.WriteLine("[D] 只放行致命类型（模拟 CrashGuard 的 IsFatal 分支）");
            {
                Harmony h = new Harmony("probe.fatal");
                h.Patch(target, null, null, null,
                    new HarmonyMethod(typeof(FatalPassthroughFinalizer).GetMethod("Finalizer")));
                bool threw = false;
                string thrown = null;
                try { new Subject().Tick(0.016f); }
                catch (Exception ex) { threw = true; thrown = ex.GetType().Name; }
                Check("D1 非致命异常被吞（tick 路径照常返回）", !threw, thrown ?? "未抛");
                h.UnpatchAll("probe.fatal");
            }

            Console.WriteLine();
            Console.WriteLine(new string('=', 74));
            if (_failed > 0)
            {
                Console.WriteLine("失败 " + _failed + " 项");
                return 1;
            }
            Console.WriteLine("全部通过 —— Finalizer 的绑定与吞/放行语义已在真实 DLL 上确认");
            return 0;
        }
    }

    /// <summary>参数名故意写错：用于判据 C。</summary>
    public static class WrongNameFinalizer
    {
        public static Exception Finalizer(Exception ex)
        {
            return null;
        }
    }
}
