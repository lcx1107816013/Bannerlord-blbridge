// PROTOTYPE — throwaway code (branch prototype/ui-probe). DELETE after the four facts are answered.
//
// Questions this prototype answers (design tree, /grill-me rounds 1-7):
//   (1) can a main-menu entry be registered from OnSubModuleLoad via
//       Module.CurrentModule.AddInitialStateOption, and start a game manager?
//   (2) can a self-written Gauntlet prefab under Modules/BlBridge/GUI/Prefabs/ be loaded
//       with GauntletLayer.LoadMovie("<name>", vm)  (movie name == prefab file name)?
//   (3) does a ViewModel's [DataSourceProperty] binding actually reach the widgets?
//   (4) in the "main menu -> our own GameState" phase, is {=key} localization loaded
//       (GameTexts.FindText / new TextObject("{=key}")), or must we LoadGameTexts by hand?
//
// Deliberately skipped: error handling, layout, persistence, hotkeys, i18n files for
// everything else.  Every interesting value is also written to proto_ui.log so the
// result is readable without looking at the screen.

using System;
using System.IO;
using System.Text;
using TaleWorlds.Core;
using TaleWorlds.Engine;
using TaleWorlds.Engine.GauntletUI;
using TaleWorlds.InputSystem;
using TaleWorlds.Library;
using TaleWorlds.Localization;
using TaleWorlds.MountAndBlade;
using TaleWorlds.MountAndBlade.CustomBattle;
using TaleWorlds.MountAndBlade.View.Screens;
using TaleWorlds.ScreenSystem;

namespace BlBridge
{
    internal static class ProtoUiLog
    {
        private static readonly object Gate = new object();

        internal static string LogPath
        {
            get { return System.IO.Path.Combine(BridgeConfig.LogDir, "proto_ui.log"); }
        }

        internal static void W(string message)
        {
            try
            {
                lock (Gate)
                {
                    if (!Directory.Exists(BridgeConfig.LogDir)) Directory.CreateDirectory(BridgeConfig.LogDir);
                    File.AppendAllText(LogPath,
                        DateTime.UtcNow.ToString("yyyy-MM-ddTHH:mm:ss.fffZ") + " | " + message + "\n",
                        new UTF8Encoding(false));
                }
            }
            catch
            {
            }
        }

        internal static void Reset()
        {
            try
            {
                if (File.Exists(LogPath)) File.Delete(LogPath);
            }
            catch
            {
            }
        }
    }

    /// <summary>Prototype entry point: one main-menu option.</summary>
    internal static class ProtoUi
    {
        internal const string MenuId = "BlBridgeProtoBattleTest";
        internal const string MovieName = "ProtoUiScreen";

        internal static void Register()
        {
            try
            {
                ProtoUiLog.Reset();
                ProtoUiLog.W("Register: Module.CurrentModule=" + (Module.CurrentModule == null ? "null" : "ok"));
                Module.CurrentModule.AddInitialStateOption(new InitialStateOption(
                    MenuId,
                    new TextObject("{=BlBridgeProto_MenuEntry}BlBridge Battle Test (PROTOTYPE)"),
                    100,
                    OnMenuClicked,
                    () => (false, new TextObject(string.Empty))));
                ProtoUiLog.W("Register: AddInitialStateOption OK (id=" + MenuId + ")");
            }
            catch (Exception ex)
            {
                ProtoUiLog.W("Register FAILED: " + ex);
            }
        }

        private static void OnMenuClicked()
        {
            try
            {
                ProtoUiLog.W("menu item clicked -> MBGameManager.StartNewGame(ProtoGameManager)");
                MBGameManager.StartNewGame(new ProtoGameManager());
            }
            catch (Exception ex)
            {
                ProtoUiLog.W("StartNewGame FAILED: " + ex);
            }
        }
    }

    /// <summary>
    /// Reuses the official CustomBattle data-loading chain (MBGameManager.LoadModuleData ->
    /// Game.CreateGame(new CustomGame()) -> ... ) and only replaces the state pushed at the end:
    /// we deliberately do NOT call base (CustomGameManager.OnLoadFinished pushes CustomBattleState).
    /// </summary>
    internal class ProtoGameManager : CustomGameManager
    {
        public override void OnLoadFinished()
        {
            try
            {
                ProtoUiLog.W("ProtoGameManager.OnLoadFinished: IsLoaded=true, pushing ProtoBattleState");
                IsLoaded = true;
                GameStateManager gsm = Game.Current.GameStateManager;
                gsm.CleanAndPushState(gsm.CreateState<ProtoBattleState>(), 0);
                ProtoUiLog.W("ProtoGameManager.OnLoadFinished: pushed");
            }
            catch (Exception ex)
            {
                ProtoUiLog.W("ProtoGameManager.OnLoadFinished FAILED: " + ex);
            }
        }
    }

    internal class ProtoBattleState : GameState
    {
        public override bool IsMusicMenuState
        {
            get { return true; }
        }

        protected override void OnInitialize()
        {
            base.OnInitialize();
            ProtoUiLog.W("ProtoBattleState.OnInitialize");
        }
    }

    [GameStateScreen(typeof(ProtoBattleState))]
    internal class ProtoBattleScreen : ScreenBase, IGameStateListener
    {
        private readonly ProtoBattleState _state;
        private GauntletLayer _layer;
        private GauntletMovieIdentifier _movie;
        private ProtoBattleVM _vm;
        private bool _movieLoaded;
        private int _firstFrameCounter = -1;

        public ProtoBattleScreen(ProtoBattleState state)
        {
            _state = state;
        }

        void IGameStateListener.OnInitialize()
        {
        }

        void IGameStateListener.OnActivate()
        {
        }

        void IGameStateListener.OnDeactivate()
        {
        }

        void IGameStateListener.OnFinalize()
        {
            if (_vm != null) _vm.OnFinalize();
        }

        protected override void OnInitialize()
        {
            base.OnInitialize();
            try
            {
                ProtoUiLog.W("PrototypeScreen.OnInitialize: creating VM + GauntletLayer");
                _vm = new ProtoBattleVM();
                _layer = new GauntletLayer("BlBridgeProto", 1, true);
                LoadMovie();
                AddLayer(_layer);
                // Focus MUST be set after the layer is part of the screen.  This revision first
                // had LoadMovie -> TrySetFocus -> AddLayer, and the clicks never reached
                // Command.Click (no ExecutePing line in proto_ui.log) because the layer had no
                // input focus.  Official CustomBattleScreen does: AddLayer in OnInitialize,
                // then IsFocusLayer + TrySetFocus in OnActivate.
                _layer.IsFocusLayer = true;
                ScreenManager.TrySetFocus(_layer);
                // Without this the layer never receives mouse input, so ButtonWidget.Command.Click
                // never fires (the first two runs: no ExecutePing in proto_ui.log).  Official
                // CustomBattleScreen: SetInputRestrictions(true, (InputUsageMask)7).
                _layer.InputRestrictions.SetInputRestrictions(true, (InputUsageMask)7);
                ProtoUiLog.W("PrototypeScreen.OnInitialize: done (layer added, focus set, input unmasked)");
            }
            catch (Exception ex)
            {
                ProtoUiLog.W("PrototypeScreen.OnInitialize FAILED: " + ex);
            }
        }

        protected override void OnActivate()
        {
            base.OnActivate();
            ProtoUiLog.W("PrototypeScreen.OnActivate");
            LoadMovie();
            if (_layer != null)
            {
                _layer.IsFocusLayer = true;
                ScreenManager.TrySetFocus(_layer);
            }
            // Copied from the official CustomBattleScreen: the engine's global loading window
            // (LoadingWindow, the War Sails splash) is enabled while a game manager loads, and it
            // is drawn ON TOP of every screen.  If nobody disables it, this screen renders fine
            // but stays invisible behind that splash -- which is exactly what the first run looked
            // like ("stuck on the loading screen").  Official code disables it on the 2nd frame.
            _firstFrameCounter = 2;
        }

        protected override void OnFrameTick(float dt)
        {
            base.OnFrameTick(dt);
            if (_firstFrameCounter >= 0)
            {
                if (_firstFrameCounter == 0)
                {
                    LoadingWindow.DisableGlobalLoadingWindow();
                    ProtoUiLog.W("OnFrameTick: LoadingWindow.DisableGlobalLoadingWindow() called");
                }
                _firstFrameCounter--;
            }
        }

        protected override void OnDeactivate()
        {
            base.OnDeactivate();
            ProtoUiLog.W("PrototypeScreen.OnDeactivate");
            UnloadMovie();
        }

        protected override void OnFinalize()
        {
            UnloadMovie();
            if (_layer != null)
            {
                RemoveLayer(_layer);
                _layer = null;
            }
            _vm = null;
            base.OnFinalize();
        }

        private void LoadMovie()
        {
            if (_movieLoaded || _layer == null || _vm == null) return;
            try
            {
                _movie = _layer.LoadMovie(ProtoUi.MovieName, _vm);
                _movieLoaded = true;
                ProtoUiLog.W("LoadMovie OK: " + ProtoUi.MovieName + " -> movie=" + (_movie == null ? "NULL" : "not-null") + ", layer=" + (_layer == null ? "NULL" : "not-null"));
            }
            catch (Exception ex)
            {
                ProtoUiLog.W("LoadMovie FAILED (" + ProtoUi.MovieName + "): " + ex);
            }
        }

        private void UnloadMovie()
        {
            if (!_movieLoaded || _layer == null) return;
            try
            {
                _layer.ReleaseMovie(_movie);
            }
            catch (Exception ex)
            {
                ProtoUiLog.W("ReleaseMovie FAILED: " + ex.Message);
            }
            _movie = null;
            _movieLoaded = false;
            _layer.IsFocusLayer = false;
            ScreenManager.TryLoseFocus(_layer);
        }
    }

    internal class ProtoBattleVM : ViewModel
    {
        private string _titleText;
        private string _literalText;
        private string _officialKeyText;
        private string _ownKeyText;
        private string _buttonText;
        private int _clicks;

        public ProtoBattleVM()
        {
            _titleText = "PROTOTYPE - BlBridge UI probe";
            _literalText = "LITERAL 中文：字面中文（不经 GameTexts）";
            _officialKeyText = "OFFICIAL KEY: " + Resolve("EcKMGoFv", "official-key-fallback");
            _ownKeyText = "OWN KEY: " + Resolve("BlBridgeProto_Hello", "own-key-fallback");
            _clicks = 0;
            _buttonText = ButtonLabel(0);
            ProtoUiLog.W("VM ctor: title=" + _titleText);
            ProtoUiLog.W("VM ctor: literal=" + _literalText);
            ProtoUiLog.W("VM ctor: officialKey=" + _officialKeyText);
            ProtoUiLog.W("VM ctor: ownKey=" + _ownKeyText);
        }

        private static string Resolve(string id, string fallback)
        {
            try
            {
                string viaTextObject = new TextObject("{=" + id + "}" + fallback).ToString();
                string viaFindText = "<n/a>";
                try
                {
                    TextObject found = GameTexts.FindText(id, null);
                    viaFindText = found == null ? "<null>" : found.ToString();
                }
                catch (Exception exFind)
                {
                    viaFindText = "<throw: " + exFind.Message + ">";
                }
                ProtoUiLog.W("GameTexts probe [" + id + "] TextObject=\"" + viaTextObject + "\" FindText=\"" + viaFindText + "\"");
                return viaTextObject;
            }
            catch (Exception ex)
            {
                ProtoUiLog.W("GameTexts probe [" + id + "] THREW: " + ex);
                return "<throw:" + fallback + ">";
            }
        }

        private static string ButtonLabel(int clicks)
        {
            return "CLICK ME (" + clicks + ")";
        }

        [DataSourceProperty]
        public string TitleText
        {
            get { return _titleText; }
            set { if (value != _titleText) { _titleText = value; OnPropertyChangedWithValue(value, "TitleText"); } }
        }

        [DataSourceProperty]
        public string LiteralText
        {
            get { return _literalText; }
            set { if (value != _literalText) { _literalText = value; OnPropertyChangedWithValue(value, "LiteralText"); } }
        }

        [DataSourceProperty]
        public string OfficialKeyText
        {
            get { return _officialKeyText; }
            set { if (value != _officialKeyText) { _officialKeyText = value; OnPropertyChangedWithValue(value, "OfficialKeyText"); } }
        }

        [DataSourceProperty]
        public string OwnKeyText
        {
            get { return _ownKeyText; }
            set { if (value != _ownKeyText) { _ownKeyText = value; OnPropertyChangedWithValue(value, "OwnKeyText"); } }
        }

        [DataSourceProperty]
        public string ButtonText
        {
            get { return _buttonText; }
            set { if (value != _buttonText) { _buttonText = value; OnPropertyChangedWithValue(value, "ButtonText"); } }
        }

        public void ExecutePing()
        {
            _clicks++;
            ProtoUiLog.W("ExecutePing: clicks=" + _clicks + " (binding round-trip works)");
            ButtonText = ButtonLabel(_clicks);
        }
    }
}
