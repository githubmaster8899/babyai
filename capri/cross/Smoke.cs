using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.IO;
using System.Net;
using System.Net.Sockets;
using System.Text;
using System.Threading;
using Avalonia;
using Avalonia.Controls;
using Avalonia.Media;
using Avalonia.Media.Imaging;
using Avalonia.Platform;

namespace CapriDesktop
{
    internal sealed class Smoke
    {
        private readonly Pet pet;
        private readonly string folder;
        private readonly List<string> checks = new List<string>();
        private readonly List<Process> shelves = new List<Process>();
        private readonly List<short[]> sounds = new List<short[]>();
        private int step;
        private double started = -1;
        private double stepStart = -1;
        private double settled = -1;
        private double coverAt = -1;
        private double hideAt = -1;
        private bool finished;
        private Process shelf;
        private Process cover;
        private PixelRect shelfAsked;
        private PixelRect coverAsked;
        private Spot shelfSpot;
        private PixelPoint hipBefore;
        private int dx;
        private int dy;
        private int arrivalsBefore;
        private double outFrom = -1;
        private TcpListener fakeVoice;
        private readonly List<string> voiceAsked = new List<string>();
        private readonly List<byte[]> spoken = new List<byte[]>();

        private bool blinked;

        internal Smoke(Pet pet, string folder)
        {
            this.pet = pet;
            this.folder = folder;
            Hands.PleaseRoll = delegate { return 1; };
            Directory.CreateDirectory(folder);
            if (File.Exists(Path.Combine(folder, "me.md"))) File.Delete(Path.Combine(folder, "me.md"));
            foreach (string old in new[] { Brain.PersonalityPath, Brain.PersonalityBackup }) if (old != null && File.Exists(old)) File.Delete(old);
            Brain.MakePersonality();
        }

        internal void Tick(double now)
        {
            if (finished) return;
            if (started < 0) started = now;
            if (stepStart < 0) stepStart = now;
            if (now - started > 150) { Finish("Timed out at step " + step + "."); return; }
            if (now - stepStart > 15) { checks.Add("INFO " + Seen()); Finish("Step " + step + " took longer than 15 seconds."); return; }
            try
            {
                if (Step(now))
                {
                    step++;
                    stepStart = now;
                }
            }
            catch (Exception error) { Finish(error.Message); }
        }

        private int Px(double dip) { return (int)Math.Round(dip * pet.DesktopScaling); }

        private PixelRect Work()
        {
            Screen screen = pet.Screens.Primary ?? pet.ScreenHere();
            if (screen == null) throw new Exception("No screen was found.");
            return screen.WorkingArea;
        }

        private bool Sitting()
        {
            Ledge seat = pet.Seat;
            return seat != null && pet.Hip.Y == seat.Y;
        }

        private bool Step(double now)
        {
            if (step == 0)
            {
                foreach (Clip clip in pet.Clips) if (!clip.Even || clip.Strip.PixelSize.Height != 384) throw new Exception("A sprite strip doesn't split into even 384 px cells.");
                checks.Add("PASS sprites: all " + pet.Clips.Count + " clips, standing and perched, load from the app and split into even cells");
                foreach (string clipName in new[] { "morning", "late", "miss" })
                {
                    byte[] hello = Pet.Hello(clipName);
                    if (hello == null || Voice.Seconds(hello) < 1 || Voice.Seconds(hello) > 5) throw new Exception("The " + clipName + " startup recording is missing or the wrong length.");
                }
                if (Brain.Greeting(8) != "Good morning, choom." || Brain.Greeting(23) != "Isn't it late, choom?" || Brain.Greeting(15) != "Miss me, choom?" || Brain.GreetingClip(15) != "miss") throw new Exception("Startup greetings came at the wrong hours.");
                checks.Add("PASS startup voice: her giggle greetings for the morning, late at night and the rest of the day are packed in the app");
                if (pet.Arrivals != 1) throw new Exception("She didn't glitch in when she started.");
                checks.Add("PASS glitch in: she glitches onto the screen when she starts, then drops");
                if (pet.ActualTransparencyLevel == WindowTransparencyLevel.Transparent) checks.Add("PASS her window is see-through");
                else checks.Add("LIMIT her window isn't see-through here (" + pet.ActualTransparencyLevel + "); on Linux that needs a compositor");
                checks.Add("INFO window list: " + pet.Desk.Name);
                return true;
            }
            if (step == 1)
            {
                if (pet.Falling || pet.Arriving) return false;
                if (!Sitting()) throw new Exception("She didn't sit down after falling in.");
                checks.Add("PASS falling: she drops in from the top of the screen and sits on " + (pet.Seat.Floor ? "the bottom of the work area" : "the top of a window below her"));
                Snap(pet.Content as Control, "pet.png");
                pet.Grab();
                if (pet.Seat != null || pet.Falling || !pet.Dragging) throw new Exception("Grabbing her didn't lift her off her seat.");
                PixelRect area = Work();
                pet.Move(pet.Seated(area.X + area.Width / 2, area.Bottom - Px(100)));
                if (!pet.Drop()) throw new Exception("Dropping her just above the floor didn't make her fall.");
                return true;
            }
            if (step == 2)
            {
                if (pet.Falling) return false;
                if (!Sitting()) throw new Exception("A dropped Capri didn't sit down again.");
                checks.Add("PASS drag and drop: grabbing her lifts her off, dropping her near a ledge makes her fall and sit on it");
                PixelPoint hip = pet.Hip;
                pet.Resize(320);
                if (pet.Hip != hip) throw new Exception("Resizing moved her off her seat: " + hip + " to " + pet.Hip + ".");
                pet.Resize(240);
                if (pet.Hip != hip) throw new Exception("Resizing back moved her off her seat.");
                checks.Add("PASS size: changing her size keeps her sitting on the same spot");
                Point? petSpot = pet.BodySpot();
                if (petSpot == null) throw new Exception("Couldn't find her body to pet.");
                PixelPoint petLeft = pet.PointToScreen(new Point(petSpot.Value.X - 12, petSpot.Value.Y));
                PixelPoint petRight = pet.PointToScreen(new Point(petSpot.Value.X + 12, petSpot.Value.Y));
                int petsBefore = pet.Pets;
                for (int i = 0; i < 6; i++) pet.Stroke(petLeft);
                pet.Stroke(petRight);
                pet.Stroke(petLeft);
                bool earlyPet = pet.Pets != petsBefore;
                for (int i = 0; i < 4; i++) pet.Stroke(i % 2 == 0 ? petRight : petLeft);
                bool smiled = pet.Pets == petsBefore + 1 && pet.Smiling && pet.Talk.Quiet;
                pet.PetLine(pet.Pets);
                bool spoke = Array.IndexOf(Pet.PetLines, pet.Talk.Bubble.Sheet.Reply) >= 0;
                for (int i = 0; i < 8; i++) pet.Stroke(i % 2 == 0 ? petRight : petLeft);
                bool cooled = pet.Pets == petsBefore + 1;
                pet.Talk.Dismiss();
                pet.ForgetPets();
                if (earlyPet || !smiled || !spoke || !cooled) throw new Exception("Petting her went wrong: early " + earlyPet + ", smiled " + smiled + ", spoke " + spoke + ", cooled " + cooled);
                checks.Add("PASS petting: rubbing the cursor back and forth over her makes her smile and say a short line, not too easily and not again for 20 s");
                pet.SetWander(3);
                pet.SetLanguage("French");
                pet.SetReplies(1);
                pet.SetRecall(16);
                pet.SetSounds(30);
                pet.Dancing = true;
                if (pet.Saved.Wander != 3 || pet.Saved.Language != "French" || Brain.Language != "French" || Brain.Wordiness != 1 || Brain.Recall != 16 || pet.Saved.Sounds != 30 || Sfx.Level != 30 || pet.Picked != "Wander: Often | Français | Medium | Medium | Quiet | Dance to music | Dance") throw new Exception("Menu settings didn't stick: " + pet.Picked);
                pet.SetDanceToMusic(false);
                if (!pet.Saved.NoDance || pet.Picked.Contains("Dance to music")) throw new Exception("Dance to music didn't turn off.");
                pet.SetDanceToMusic(true);
                pet.SetWander(0);
                pet.SetLanguage("English");
                pet.SetReplies(0);
                pet.SetRecall(8);
                pet.SetSounds(60);
                pet.Dancing = false;
                if (Brain.Language != null || pet.Saved.Language != null || pet.Picked != "Stay put | English | Short | Short | Normal | Dance to music") throw new Exception("Menu settings didn't go back: " + pet.Picked);
                Func<string, string[], string> runWas = Music.Run;
                List<string> ran = new List<string>();
                string status = "Playing";
                string apple = "playing\n";
                try
                {
                    Music.Run = delegate(string file, string[] args)
                    {
                        string line = file + " " + string.Join(" ", args);
                        ran.Add(line);
                        if (line.Contains("ListNames")) return "method return\n   array [\n      string \"org.freedesktop.DBus\"\n      string \"org.mpris.MediaPlayer2.chromium.instance4021\"\n      string \"org.mpris.MediaPlayer2.spotify\"\n   ]\n";
                        if (line.Contains("PlaybackStatus")) return "method return\n   variant       string \"" + status + "\"\n";
                        if (file == "pgrep") return line.EndsWith(" Spotify", StringComparison.Ordinal) ? "4242\n" : null;
                        if (file == "osascript") return line.Contains("\"Spotify\"") ? apple : "";
                        return null;
                    };
                    bool linuxOn = Music.Linux();
                    bool macOn = Music.Mac();
                    status = "Paused";
                    apple = "paused\n";
                    bool linuxOff = Music.Linux();
                    bool macOff = Music.Mac();
                    string asked = string.Join(" / ", ran);
                    if (!linuxOn || !macOn || linuxOff || macOff || asked.Contains("chromium") || !asked.Contains("--dest=org.mpris.MediaPlayer2.spotify") || !asked.Contains("pgrep -x Music") || asked.Contains("tell application \"Music\"") || !asked.Contains("tell application \"Spotify\" to get player state")) throw new Exception("Music detection read players wrong: " + asked);
                }
                finally { Music.Run = runWas; }
                checks.Add("PASS music: Linux reads music players over D-Bus (not browsers), Mac asks Spotify and Music only when they're already running");
                Action<string, string[]> launchWas = Limbs.Launch;
                int patienceWas = Limbs.Patience;
                int settleWas = Limbs.Settle;
                Func<List<InstalledApp>> catalogWas = Hands.Catalog;
                List<string> did = new List<string>();
                string tempApps = Path.Combine(folder, "apps");
                try
                {
                    Limbs.Patience = 2;
                    Limbs.Settle = 0;
                    Limbs.Launch = delegate(string file, string[] args) { did.Add("launch " + file + " " + string.Join(" ", args)); };
                    Music.Run = delegate(string file, string[] args)
                    {
                        string line = file + " " + string.Join(" ", args);
                        did.Add(line);
                        if (file == "pgrep") return line.EndsWith(" Spotify", StringComparison.Ordinal) ? "4242\n" : null;
                        if (line.Contains("ListNames")) return "   array [\n      string \"org.mpris.MediaPlayer2.firefox.instance_1_9\"\n      string \"org.mpris.MediaPlayer2.spotify\"\n   ]\n";
                        if (line.Contains("PlaybackStatus")) return "   variant       string \"Playing\"\n";
                        if (line.Contains("Metadata")) return "   variant       array [\n         dict entry(\n            string \"xesam:artist\"\n            variant                array [\n                  string \"Chase Atlantic\"\n               ]\n         )\n         dict entry(\n            string \"xesam:title\"\n            variant                string \"Consume\"\n         )\n      ]\n";
                        if (file == "osascript" && line.Contains("current track")) return "Petite Meller - In Love With Myself\n";
                        if (file == "pactl") return null;
                        return "";
                    };
                    Directory.CreateDirectory(Path.Combine(tempApps, "Spotify.app"));
                    File.WriteAllLines(Path.Combine(tempApps, "spotify.desktop"), new[] { "[Desktop Entry]", "Type=Application", "Name=Spotify", "Exec=spotify %U" });
                    File.WriteAllLines(Path.Combine(tempApps, "helper.desktop"), new[] { "[Desktop Entry]", "Type=Application", "Name=Helper", "Exec=helper", "NoDisplay=true" });
                    List<InstalledApp> linuxApps = Limbs.LinuxApps(new[] { tempApps });
                    List<InstalledApp> macApps = Limbs.MacApps(new[] { tempApps });
                    if (linuxApps.Count != 1 || linuxApps[0].Name != "Spotify" || linuxApps[0].Id != "exec:spotify" || macApps.Count != 1 || macApps[0].Id != Path.Combine(tempApps, "Spotify.app")) throw new Exception("Installed apps were read wrong.");
                    Hands.Catalog = delegate { return Limbs.LinuxApps(new[] { tempApps }); };
                    Hands.Forget();
                    string opened = Hands.Do("open_app", new ToolArguments { Name = "spotify" });
                    string searched = Hands.Do("open_website", new ToolArguments { Site = "youtube", Search = "lofi beats" });
                    string skipped = Hands.Do("media", new ToolArguments { Action = "next" });
                    string snipped = Hands.Do("shortcut", new ToolArguments { Name = "screenshot" });
                    Limbs.Key(0xB0, true);
                    Limbs.Key(0xAF, true);
                    Limbs.Key(0xAD, false);
                    Limbs.Open("spotify:track:2iyaHQlYrR8Eyr4wOmOT5D", true);
                    Limbs.Open("spotify:track:2iyaHQlYrR8Eyr4wOmOT5D", false);
                    string macSong = Limbs.Song(true);
                    string linuxSong = Limbs.Song(false);
                    string log = string.Join(" / ", did);
                    if (opened != "Opened Spotify." || !log.Contains("launch /bin/sh -c spotify") || searched != "Searched youtube.com for lofi beats." || !log.Contains((OperatingSystem.IsMacOS() ? "launch open " : "launch xdg-open ") + "https://www.youtube.com/results?search_query=lofi%20beats")) throw new Exception("Opening apps or sites went wrong: " + opened + " / " + searched + " / " + log);
                    if (skipped != "Skipped to the next track." || !log.Contains(OperatingSystem.IsMacOS() ? "osascript -e tell application \"Spotify\" to next track" : "--dest=org.mpris.MediaPlayer2.spotify /org/mpris/MediaPlayer2 org.mpris.MediaPlayer2.Player.Next") || !snipped.Contains("only work on Windows")) throw new Exception("Media keys or shortcuts went wrong: " + skipped + " / " + snipped + " / " + log);
                    if (!log.Contains("osascript -e tell application \"Spotify\" to next track") || !log.Contains("+ 2)") || !log.Contains("pactl set-sink-mute @DEFAULT_SINK@ toggle / amixer -q sset Master toggle")) throw new Exception("Mac or Linux media keys went wrong: " + log);
                    if (!log.Contains("play track \"spotify:track:2iyaHQlYrR8Eyr4wOmOT5D\"") || !log.Contains("OpenUri string:spotify:track:2iyaHQlYrR8Eyr4wOmOT5D") || log.Contains("launch open spotify:") || log.Contains("launch xdg-open spotify:")) throw new Exception("Spotify tracks didn't play directly: " + log);
                    if (macSong != "Petite Meller - In Love With Myself" || linuxSong != "Chase Atlantic - Consume") throw new Exception("Now playing was read wrong: " + macSong + " / " + linuxSong);
                }
                finally
                {
                    Music.Run = runWas;
                    Limbs.Launch = launchWas;
                    Limbs.Patience = patienceWas;
                    Limbs.Settle = settleWas;
                    Hands.Catalog = catalogWas;
                    Hands.Forget();
                }
                checks.Add("PASS hands: she opens apps (.app folders, .desktop files) and sites, skips and plays Spotify tracks, reads what's playing and turns the volume on Mac and Linux; Windows-only shortcuts say so");
                pet.ShowBrains(new[] { "gemma4:e4b", "qwen3:8b" });
                int brains = pet.BrainChoices;
                pet.PickBrain("qwen3:8b");
                bool picked = pet.Brain.Model == "qwen3:8b" && pet.Saved.Model == "qwen3:8b";
                pet.ShowBrains(null);
                if (brains != 2 || !picked || pet.BrainChoices != 1) throw new Exception("The Brain menu didn't list or pick models.");
                checks.Add("PASS settings menu: wander, dance, language, reply length, chat memory and the brain picker change her and show which is picked");
                pet.AskNickname();
                bool askedName = pet.Talk.Typing && pet.Talk.Reply != null && pet.Talk.Reply.StartsWith("What should I call you?", StringComparison.Ordinal);
                pet.Talk.Submit("  Lachy!!  ");
                bool namedRight = !pet.Talk.Typing && Brain.Nickname == "Lachy" && pet.Saved.Nickname == "Lachy" && pet.Talk.Reply == "Got it, Lachy." && Brain.Character.IndexOf("call them: Lachy.", StringComparison.Ordinal) >= 0;
                pet.SetNickname("shithead");
                bool refusedName = Brain.Nickname == "Lachy" && pet.Talk.Reply == Brain.NotCalling;
                pet.SetNickname("choom");
                bool clearedName = Brain.Nickname == null && pet.Saved.Nickname == null && pet.Talk.Reply == "Back to choom, then." && Brain.Character.IndexOf("set a nickname", StringComparison.Ordinal) < 0;
                if (!askedName || !namedRight || !refusedName || !clearedName) throw new Exception("The nickname went wrong: asked " + askedName + ", named " + namedRight + ", refused " + refusedName + ", cleared " + clearedName);
                checks.Add("PASS nickname: Settings > What should she call you? asks in her typing box, saves a cleaned-up name she says back, a rude one is refused, and choom goes back to choom");
                pet.Hear("remember I love Hollow Knight");
                string saved = pet.Talk.Reply;
                string kept = Brain.Notes();
                pet.Hear("forget about hollow knight");
                string forgot = pet.Talk.Reply;
                string notesPath = pet.OpenNotes();
                if (saved != "Saved to my notes: They love Hollow Knight." || kept != "- They love Hollow Knight" || forgot != "Forgot my note about hollow knight." || Brain.Notes() != "" || notesPath == null || !File.ReadAllText(notesPath).StartsWith("# What Capri remembers", StringComparison.Ordinal)) throw new Exception("Her notes didn't work: " + saved + " / " + kept + " / " + forgot);
                checks.Add("PASS notes: remember and forget save to me.md here too, and Open her notes makes the file");
                bool personalityMade = Brain.PersonalityPath == Path.Combine(folder, "personality.md") && File.ReadAllText(Brain.PersonalityPath) == Brain.PersonalityFile && Brain.Persona == Brain.DefaultPersonality + "\n" + Brain.Machinery;
                File.Delete(Brain.PersonalityPath);
                string personalityPath = pet.OpenPersonality();
                bool personalityOpened = personalityPath == Brain.PersonalityPath && File.ReadAllText(personalityPath) == Brain.PersonalityFile && Brain.PersonalityNote == Brain.HerDefault;
                File.WriteAllText(personalityPath, "<!-- mine -->\nYou are Capri, but grumpy.\n");
                bool personalityEdited = Brain.Persona == "You are Capri, but grumpy.\n" + Brain.Machinery && Brain.Character.StartsWith("You are Capri, but grumpy.\n", StringComparison.Ordinal) && Brain.PersonalityNote == Brain.YourEdit;
                pet.ResetPersonality();
                bool personalityReset = pet.Talk.Reply == Brain.ResetKept && Brain.Personality == Brain.DefaultPersonality && File.ReadAllText(Brain.PersonalityBackup) == "<!-- mine -->\nYou are Capri, but grumpy.\n";
                File.WriteAllText(personalityPath, "");
                bool personalityEmpty = Brain.Personality == Brain.DefaultPersonality && Brain.PersonalityNote == Brain.BlankFile;
                File.WriteAllText(personalityPath, Brain.PersonalityFile);
                File.Delete(Brain.PersonalityBackup);
                if (!personalityMade || !personalityOpened || !personalityEdited || !personalityReset || !personalityEmpty) throw new Exception("personality.md went wrong: made " + personalityMade + ", opened " + personalityOpened + ", edited " + personalityEdited + ", reset " + personalityReset + ", empty " + personalityEmpty);
                checks.Add("PASS personality.md: it starts as her default next to me.md, an edit reaches her prompt on her next reply, an empty one falls back to her default, and Reset puts her default back and keeps theirs as personality.backup.md");
                List<short[]> typedHeard = new List<short[]>();
                Sfx.TypeOut = delegate(short[] typed) { typedHeard.Add(typed); };
                Sfx.Untyped = delegate { };
                pet.Hear("remember I love Silksong");
                int typedOn = typedHeard.Count;
                double typedSeconds = typedOn == 1 ? (double)typedHeard[0].Length / Sfx.Rate : 0;
                double typedWanted = pet.Talk.Reply.Length / Talk.LettersPerSecond + 0.05;
                pet.SetTyping(false);
                bool typingOff = !pet.TypingSound && !Sfx.Typing;
                pet.Hear("forget about silksong");
                int typedOff = typedHeard.Count;
                pet.SetTyping(true);
                pet.AutoTyping();
                Sfx.TypeOut = null;
                Sfx.Untyped = null;
                if (typedOn != 1 || Math.Abs(typedSeconds - typedWanted) > 0.02 || !typingOff || typedOff != 1 || typedHeard.Count != 2 || !pet.TypingSound || Brain.Notes() != "" || !Sfx.Typing) throw new Exception("Typing sound went wrong: " + typedOn + " | " + typedSeconds + " | " + typingOff + " | " + typedOff + " | " + typedHeard.Count);
                checks.Add("PASS typing sound: a text-only reply plays soft typing ticks, Settings > Sounds > Typing sound turns them off");
                pet.Talk.Dismiss();
                pet.Think = null;
                pet.Brain.Model = null;
                pet.Hear("hello?");
                if (pet.Talk.Reply != Pet.Offline) throw new Exception("No brain didn't explain how to get one: " + pet.Talk.Reply);
                checks.Add("PASS no brain: she says to install Ollama and pull a model");
                pet.Talk.Dismiss();
                pet.Think = delegate(string words) { return words == "hey" ? "Just vibing, choom." : "wrong words"; };
                pet.Talk.Pill.Press(1);
                if (!pet.Talk.Typing) throw new Exception("The pencil didn't open the typing box.");
                pet.Talk.Submit("hey");
                return true;
            }
            if (step == 3)
            {
                if (!pet.Talk.Done) return false;
                if (settled < 0) settled = now;
                if (now - settled < 0.3) return false;
                if (pet.Talk.Shown != "Just vibing, choom.") throw new Exception("Her reply showed wrong: " + pet.Talk.Shown);
                Bubble bubble = pet.Talk.Bubble;
                if (bubble.Position.Y + (int)Math.Round(bubble.Height * bubble.DesktopScaling) > pet.Head.Y + 2) throw new Exception("Her bubble covered her head.");
                if (!pet.Talk.Pill.IsVisible) throw new Exception("The pencil and mic pill didn't show under her while she talked.");
                Snap(pet.Talk.Face, "talk.png");
                Snap(pet.Talk.Typer.Content as Control, "typer.png");
                Snap(pet.Talk.Pill.Content as Control, "pill.png");
                Scene("desktop.png", new Window[] { pet, bubble, pet.Talk.Pill }, new PixelRect[0]);
                checks.Add("PASS chat: the typing box sends a line to her brain, the reply types out in a bubble above her head");
                pet.Talk.Dismiss();
                PixelRect area = Work();
                shelfAsked = new PixelRect(area.X + area.Width / 2 - Px(350), area.Y + area.Height / 2 - Px(140), Px(700), Px(360));
                shelf = Shelf(shelfAsked);
                return true;
            }
            if (step == 4)
            {
                shelfSpot = Near(shelfAsked, true);
                if (shelfSpot == null) return false;
                pet.StandUp();
                pet.Move(pet.Seated(shelfSpot.Bounds.X + Px(220), shelfSpot.Bounds.Y - Px(12)));
                if (!pet.Drop()) throw new Exception("She couldn't reach the test shelf 12 px under her.");
                return true;
            }
            if (step == 5)
            {
                if (pet.Falling) return false;
                Ledge seat = pet.Seat;
                if (seat == null || seat.Window != shelfSpot.Window || pet.Hip.Y != shelfSpot.Bounds.Y) throw new Exception("She didn't land on the test shelf's top edge.");
                if (pet.Showing != pet.PerchGlitch) throw new Exception("Sitting on the shelf didn't glitch her on.");
                checks.Add("PASS sitting: dropped over a window, she lands on its top edge and glitches into her seat");
                Scene("shelf.png", new Window[] { pet }, new[] { shelfSpot.Bounds });
                hipBefore = pet.Hip;
                dx = Px(120);
                dy = Px(60);
                Send(shelf, "move " + (shelfAsked.X + dx) + " " + (shelfAsked.Y + dy));
                return true;
            }
            if (step == 6)
            {
                Ledge seat = pet.Seat;
                if (seat == null) throw new Exception("She stood up when the shelf moved.");
                if (seat.Y != shelfSpot.Bounds.Y + dy) return false;
                if (pet.Hip.Y != seat.Y || pet.Hip.X != hipBefore.X + dx) throw new Exception("She didn't move with the shelf: hip " + pet.Hip + ", expected " + new PixelPoint(hipBefore.X + dx, seat.Y) + ".");
                checks.Add("PASS following: when the window moves, she moves with it and stays seated");
                coverAsked = new PixelRect(pet.Hip.X - Px(130), seat.Y - Px(60), Px(260), Px(200));
                cover = Shelf(coverAsked);
                return true;
            }
            if (step == 7)
            {
                if (coverAt < 0)
                {
                    if (Near(coverAsked, false) == null) return false;
                    coverAt = now;
                }
                if (pet.Seat != null) return false;
                double waited = now - coverAt;
                if (waited < 0.45 || waited > 2) throw new Exception("She stood up " + waited.ToString("0.00") + " s after a window covered her seat; it should be about 0.7 s.");
                if (pet.Falling) throw new Exception("Standing up made her fall.");
                checks.Add("PASS covered: a window over her seat makes her stand up after " + waited.ToString("0.0") + " s");
                Send(cover, "quit");
                return true;
            }
            if (step == 8)
            {
                if (!cover.HasExited) return false;
                shelfSpot = Near(new PixelRect(shelfAsked.X + dx, shelfAsked.Y + dy, shelfAsked.Width, shelfAsked.Height), true);
                if (shelfSpot == null) return false;
                pet.Move(pet.Seated(shelfSpot.Bounds.X + Px(220), shelfSpot.Bounds.Y - Px(12)));
                if (!pet.Drop()) throw new Exception("She couldn't get back onto the shelf.");
                return true;
            }
            if (step == 9)
            {
                if (pet.Falling) return false;
                if (pet.Seat == null || pet.Seat.Window != shelfSpot.Window) throw new Exception("She didn't sit back on the shelf.");
                Send(shelf, "hide");
                hideAt = now;
                return true;
            }
            if (step == 10)
            {
                if (pet.Seat != null) return false;
                double waited = now - hideAt;
                if (waited > 1.5) throw new Exception("She took " + waited.ToString("0.00") + " s to stand up after her window hid.");
                checks.Add("PASS window gone: when her window hides, she stands up after " + waited.ToString("0.0") + " s");
                Send(shelf, "show");
                return true;
            }
            if (step == 11)
            {
                shelfSpot = Near(new PixelRect(shelfAsked.X + dx, shelfAsked.Y + dy, shelfAsked.Width, shelfAsked.Height), true);
                if (shelfSpot == null) return false;
                pet.StandUp();
                pet.Move(pet.Seated(shelfSpot.Bounds.X + Px(200), shelfSpot.Bounds.Y - Px(12)));
                if (!pet.Drop()) throw new Exception("She couldn't get back onto the shown shelf.");
                return true;
            }
            if (step == 12)
            {
                if (pet.Falling) return false;
                if (pet.Seat == null || pet.Seat.Window != shelfSpot.Window) throw new Exception("She didn't sit on the shelf before walking.");
                hipBefore = pet.Hip;
                if (!pet.Stroll() || !pet.Walking) throw new Exception("She couldn't start walking along the shelf.");
                if (pet.Hip.Y != shelfSpot.Bounds.Y) throw new Exception("Standing up to walk lifted her off the shelf: hip " + pet.Hip + ".");
                return true;
            }
            if (step == 13)
            {
                if (pet.Walking)
                {
                    if (pet.Hip.Y != pet.Seat.Y) throw new Exception("Her feet left the shelf while walking.");
                    return false;
                }
                if (!Sitting() || pet.Seat.Window != shelfSpot.Window || pet.Hip.X == hipBefore.X) throw new Exception("She didn't walk and sit back down on the shelf.");
                checks.Add("PASS walking: she gets up, walks along her window with her feet on it, and sits back down " + Math.Abs(pet.Hip.X - hipBefore.X) + " px away");
                hipBefore = pet.Hip;
                if (!pet.Blink() || !pet.Leaving) throw new Exception("She couldn't teleport along the shelf.");
                return true;
            }
            if (step == 14)
            {
                if (pet.Leaving || pet.Arriving || pet.Falling) return false;
                if (!blinked)
                {
                    if (pet.Walking || pet.Seat == null || pet.Seat.Window != shelfSpot.Window || pet.Hip.Y != shelfSpot.Bounds.Y || pet.Hip.X == hipBefore.X) throw new Exception("She didn't teleport to a new spot on the same shelf: hip " + pet.Hip + ".");
                    checks.Add("PASS teleporting: wander glitches her out and back in " + Math.Abs(pet.Hip.X - hipBefore.X) + " px along her window instead of walking");
                    blinked = true;
                    if (!pet.Wander() || !pet.Leaving) throw new Exception("She had nowhere to hop to.");
                    return false;
                }
                if (pet.Seat == null || (pet.Seat.Window == shelfSpot.Window && pet.Seat.Y == shelfSpot.Bounds.Y)) throw new Exception("She didn't glitch over to a new spot.");
                checks.Add("PASS hopping: wander glitches her out and back in on " + (pet.Seat.Floor ? "the bottom of the screen" : "another window"));
                pet.HearMusic(true);
                return true;
            }
            if (step == 15)
            {
                if (pet.Showing != pet.SeatDance) return false;
                Snap(pet.Content as Control, "dance.png");
                pet.HearMusic(false);
                checks.Add("PASS dancing: she dances in her seat while music plays");
                outFrom = now;
                Sfx.Out = sounds.Add;
                pet.HideAway();
                return true;
            }
            if (step == 16)
            {
                if (pet.IsVisible) return false;
                if (now - outFrom < 0.6) throw new Exception("Her glitch-out ended after " + (now - outFrom).ToString("0.00") + " s.");
                if (!pet.Away || sounds.Count != 1 || sounds[0].Length != Sfx.Make(Cue.Leave, 60).Length) throw new Exception("Her glitch-out didn't play its sound once: " + sounds.Count);
                checks.Add("PASS glitch out: Hide her plays her glitch-out and its sound for " + (now - outFrom).ToString("0.0") + " s, then her window goes");
                arrivalsBefore = pet.Arrivals;
                pet.ComeBack();
                if (!pet.IsVisible || pet.Away || !pet.Arriving || pet.Arrivals != arrivalsBefore + 1) throw new Exception("Show her didn't glitch her back in.");
                return true;
            }
            if (step == 17)
            {
                if (pet.Arriving || pet.Falling) return false;
                if (!Sitting()) throw new Exception("After Show her she didn't sit down again.");
                if (sounds.Count != 2 || sounds[1].Length != Sfx.Make(Cue.Arrive, 60).Length) throw new Exception("Show her didn't play the arrive sound: " + sounds.Count);
                checks.Add("PASS show: Show her glitches her back in with its sound and she drops onto a seat");
                Voice.Host = FakeVoice();
                Voice.Playback = delegate(byte[] wav) { lock (spoken) spoken.Add(wav); };
                Voice.Silence = delegate { };
                pet.Talk.Dismiss();
                if (pet.VoiceStatus != "no key | no voice | quiet") throw new Exception("She started with a voice: " + pet.VoiceStatus);
                pet.AskVoiceKey();
                if (!pet.Talk.Typing || !pet.Talk.Typer.Secret || pet.Talk.Reply == null || pet.Talk.Reply.IndexOf("ElevenLabs API key", StringComparison.Ordinal) < 0) throw new Exception("Add ElevenLabs key didn't ask for the key in a hidden box.");
                pet.Talk.Submit("bad-key");
                return true;
            }
            if (step == 18)
            {
                if (pet.Talk.Reply == null) return false;
                if (pet.Talk.Reply != Voice.RejectedNew || pet.VoiceStatus != "no key | no voice | quiet" || File.Exists(Path.Combine(folder, "voice-key")) || pet.Talk.Typer.Secret) throw new Exception("A rejected ElevenLabs key wasn't explained, or was kept: " + pet.Talk.Reply + " | " + pet.VoiceStatus);
                pet.AskVoiceKey();
                pet.Talk.Submit("good-key-1234");
                return true;
            }
            if (step == 19)
            {
                if (pet.Talk.Reply == null) return false;
                string keyPath = Path.Combine(folder, "voice-key");
                string[] asked;
                lock (voiceAsked) asked = voiceAsked.ToArray();
                int voiced;
                lock (spoken) voiced = spoken.Count;
                bool shut = OperatingSystem.IsWindows() || File.GetUnixFileMode(keyPath) == (UnixFileMode.UserRead | UnixFileMode.UserWrite);
                string last = asked.Length == 0 ? "" : asked[asked.Length - 1];
                if (pet.Talk.Reply != "Key saved. I'll talk as Capri Test. Change it under Settings > Voice." || !File.Exists(keyPath) || File.ReadAllText(keyPath) != "good-key-1234" || !shut || pet.VoiceStatus != "key 1234 | Capri Test | speaking" || voiced != 1 || !last.StartsWith("POST /v1/text-to-speech/v1?", StringComparison.Ordinal) || last.IndexOf(" good-key-1234 ", StringComparison.Ordinal) < 0) throw new Exception("Saving a good ElevenLabs key went wrong: " + pet.Talk.Reply + " | " + pet.VoiceStatus + " | " + voiced + " | " + string.Join(" / ", asked));
                string voiceMenu = pet.VoiceMenu;
                if (voiceMenu != "*Use ElevenLabs | - | *Capri Test | Adam | - | New key... | Remove key ending 1234") throw new Exception("The Voice menu listed the wrong things: " + voiceMenu);
                checks.Add("PASS voice key: Settings > Voice asks for the key in a hidden box, explains and drops a rejected key, keeps a good one in a private file and picks their own voice first");
                if (pet.TypingSound) throw new Exception("Typing sound stayed on while her ElevenLabs voice works.");
                pet.Think = delegate(string words) { return "Preem question, choom."; };
                pet.Hear("talk to me");
                return true;
            }
            if (step == 20)
            {
                if (pet.Talk.Reply == null) return false;
                string[] asked;
                lock (voiceAsked) asked = voiceAsked.ToArray();
                int voiced;
                lock (spoken) voiced = spoken.Count;
                string last = asked.Length == 0 ? "" : asked[asked.Length - 1];
                if (pet.Talk.Reply != "Preem question, choom." || voiced != 2 || last.IndexOf("question", StringComparison.Ordinal) < 0 || Math.Abs(pet.Talk.Pace - 22) > 1.5) throw new Exception("Her reply wasn't spoken at the voice's pace: " + pet.Talk.Reply + " | " + voiced + " | " + pet.Talk.Pace + " | " + last);
                pet.SetSpeak(false);
                pet.Hear("again");
                return true;
            }
            if (step == 21)
            {
                if (pet.Talk.Reply == null) return false;
                int voiced;
                lock (spoken) voiced = spoken.Count;
                if (voiced != 2 || pet.VoiceStatus != "key 1234 | Capri Test | quiet" || !pet.TypingSound) throw new Exception("Speak replies off still spoke, or typing stayed off: " + voiced + " | " + pet.VoiceStatus);
                pet.ForgetVoice();
                if (File.Exists(Path.Combine(folder, "voice-key")) || pet.VoiceStatus != "no key | v1 | quiet" || pet.VoiceMenu != "Add ElevenLabs key...") throw new Exception("Remove key didn't remove it: " + pet.VoiceStatus + " | " + pet.VoiceMenu);
                checks.Add("PASS voice: replies are spoken through ElevenLabs and type out at the voice's pace, Use ElevenLabs off keeps her quiet, and Remove key deletes the file");
                fakeVoice.Stop();
                Send(shelf, "quit");
                Finish(null);
                return true;
            }
            return false;
        }

        private string FakeVoice()
        {
            fakeVoice = new TcpListener(IPAddress.Loopback, 0);
            fakeVoice.Start();
            TcpListener listener = fakeVoice;
            Thread serve = new Thread(delegate()
            {
                while (true)
                {
                    TcpClient client;
                    try { client = listener.AcceptTcpClient(); }
                    catch (Exception) { return; }
                    try { using (client) Serve(client.GetStream()); }
                    catch (Exception) { }
                }
            });
            serve.IsBackground = true;
            serve.Start();
            return "http://127.0.0.1:" + ((IPEndPoint)fakeVoice.LocalEndpoint).Port;
        }

        private void Serve(NetworkStream stream)
        {
            List<byte> read = new List<byte>();
            byte[] chunk = new byte[4096];
            int end = -1;
            while (end < 0)
            {
                int got = stream.Read(chunk, 0, chunk.Length);
                if (got <= 0) return;
                for (int i = 0; i < got; i++) read.Add(chunk[i]);
                for (int i = 3; i < read.Count && end < 0; i++) if (read[i - 3] == 13 && read[i - 2] == 10 && read[i - 1] == 13 && read[i] == 10) end = i + 1;
            }
            string head = Encoding.ASCII.GetString(read.ToArray(), 0, end);
            string[] lines = head.Split(new[] { "\r\n" }, StringSplitOptions.None);
            string[] first = lines[0].Split(' ');
            string key = "";
            int length = 0;
            foreach (string line in lines)
            {
                int colon = line.IndexOf(':');
                if (colon < 0) continue;
                string name = line.Substring(0, colon).Trim().ToLowerInvariant();
                string value = line.Substring(colon + 1).Trim();
                if (name == "xi-api-key") key = value;
                if (name == "content-length") length = int.Parse(value);
            }
            while (read.Count - end < length)
            {
                int got = stream.Read(chunk, 0, chunk.Length);
                if (got <= 0) break;
                for (int i = 0; i < got; i++) read.Add(chunk[i]);
            }
            string body = Encoding.UTF8.GetString(read.ToArray(), end, read.Count - end);
            lock (voiceAsked) voiceAsked.Add(first[0] + " " + first[1] + " " + key + " " + body);
            int status = 200;
            string type = "application/json";
            byte[] reply;
            if (key == "bad-key") { status = 401; reply = Encoding.UTF8.GetBytes("{\"detail\":{\"status\":\"invalid_api_key\",\"message\":\"Invalid API key\"}}"); }
            else if (first[1].StartsWith("/v2/voices", StringComparison.Ordinal)) reply = Encoding.UTF8.GetBytes("{\"voices\":[{\"voice_id\":\"p1\",\"name\":\"Adam\",\"category\":\"premade\"},{\"voice_id\":\"v1\",\"name\":\"Capri Test\",\"category\":\"cloned\"}],\"has_more\":false}");
            else if (first[1].StartsWith("/v1/models", StringComparison.Ordinal)) reply = Encoding.UTF8.GetBytes("[]");
            else if (first[1].StartsWith("/v1/text-to-speech/", StringComparison.Ordinal))
            {
                short[] tone = new short[24000];
                for (int i = 0; i < tone.Length; i++) tone[i] = (short)(Math.Sin(i * 0.05) * 3000);
                reply = Voice.Wave(tone, 24000);
                type = "audio/wav";
            }
            else { status = 404; reply = Encoding.UTF8.GetBytes("{}"); }
            byte[] top = Encoding.ASCII.GetBytes("HTTP/1.1 " + status + " " + (status == 200 ? "OK" : "Error") + "\r\nContent-Type: " + type + "\r\nContent-Length: " + reply.Length + "\r\nConnection: close\r\n\r\n");
            stream.Write(top, 0, top.Length);
            stream.Write(reply, 0, reply.Length);
            stream.Flush();
        }

        private Process Shelf(PixelRect rect)
        {
            ProcessStartInfo start = new ProcessStartInfo(Environment.ProcessPath, "--shelf " + rect.X + " " + rect.Y + " " + (int)Math.Round(rect.Width / pet.DesktopScaling) + " " + (int)Math.Round(rect.Height / pet.DesktopScaling));
            start.UseShellExecute = false;
            start.RedirectStandardInput = true;
            Process process = Process.Start(start);
            if (process == null) throw new Exception("The test shelf window didn't start.");
            shelves.Add(process);
            return process;
        }

        private static void Send(Process process, string line)
        {
            if (process != null && !process.HasExited) process.StandardInput.WriteLine(line);
        }

        private string Seen()
        {
            List<string> parts = new List<string>();
            foreach (Spot spot in pet.Windows()) parts.Add(spot.Window + " " + spot.Bounds + (spot.Seat ? " seat" : ""));
            return "window list " + pet.Desk.Name + (pet.DeskTrouble == null ? "" : " after " + pet.DeskTrouble) + ", scale " + pet.Desk.Scale.ToString("0.###") + ", shelf asked " + shelfAsked + " " + (shelf == null ? "not started" : shelf.HasExited ? "exited " + shelf.ExitCode : "running") + ", saw " + (parts.Count == 0 ? "no windows" : string.Join("; ", parts)) + ", " + pet.Desk.Debug();
        }

        private Spot Near(PixelRect asked, bool seat)
        {
            Spot best = null;
            int bestFar = int.MaxValue;
            foreach (Spot spot in pet.Windows())
            {
                if (seat && !spot.Seat) continue;
                int far = Math.Abs(spot.Bounds.X - asked.X) + Math.Abs(spot.Bounds.Y - asked.Y) + Math.Abs(spot.Bounds.Width - asked.Width);
                if (far < Px(120) && far < bestFar) { best = spot; bestFar = far; }
            }
            return best;
        }

        private void Snap(Control control, string name)
        {
            if (control == null) return;
            PixelSize size = new PixelSize(Math.Max(1, (int)Math.Ceiling(control.Bounds.Width)), Math.Max(1, (int)Math.Ceiling(control.Bounds.Height)));
            using (RenderTargetBitmap shot = new RenderTargetBitmap(size, new Vector(96, 96)))
            {
                shot.Render(control);
                shot.Save(Path.Combine(folder, name));
            }
        }

        private void Scene(string name, Window[] windows, PixelRect[] boxes)
        {
            int left = int.MaxValue;
            int top = int.MaxValue;
            int right = int.MinValue;
            int bottom = int.MinValue;
            double scale = pet.DesktopScaling;
            foreach (Window window in windows)
            {
                left = Math.Min(left, window.Position.X);
                top = Math.Min(top, window.Position.Y);
                right = Math.Max(right, window.Position.X + (int)Math.Ceiling(window.Bounds.Width * scale));
                bottom = Math.Max(bottom, window.Position.Y + (int)Math.Ceiling(window.Bounds.Height * scale));
            }
            foreach (PixelRect box in boxes)
            {
                left = Math.Min(left, box.X);
                top = Math.Min(top, box.Y);
                right = Math.Max(right, box.Right);
                bottom = Math.Max(bottom, box.Bottom);
            }
            double pad = 40;
            PixelSize size = new PixelSize((int)Math.Ceiling((right - left) / scale + pad * 2), (int)Math.Ceiling((bottom - top) / scale + pad * 2));
            using (RenderTargetBitmap scene = new RenderTargetBitmap(size, new Vector(96, 96)))
            {
                using (DrawingContext context = scene.CreateDrawingContext())
                {
                    context.FillRectangle(new SolidColorBrush(Color.FromRgb(238, 240, 243)), new Rect(0, 0, size.Width, size.Height));
                    foreach (PixelRect box in boxes)
                    {
                        Rect place = new Rect(pad + (box.X - left) / scale, pad + (box.Y - top) / scale, box.Width / scale, box.Height / scale);
                        context.FillRectangle(new SolidColorBrush(Color.FromRgb(200, 204, 210)), place);
                        context.FillRectangle(new SolidColorBrush(Color.FromRgb(120, 126, 136)), new Rect(place.X, place.Y, place.Width, Math.Min(32, place.Height)));
                    }
                    foreach (Window window in windows)
                    {
                        Control face = window.Content as Control;
                        if (face == null || !window.IsVisible) continue;
                        PixelSize part = new PixelSize(Math.Max(1, (int)Math.Ceiling(face.Bounds.Width)), Math.Max(1, (int)Math.Ceiling(face.Bounds.Height)));
                        RenderTargetBitmap shot = new RenderTargetBitmap(part, new Vector(96, 96));
                        shot.Render(face);
                        context.DrawImage(shot, new Rect(pad + (window.Position.X - left) / scale, pad + (window.Position.Y - top) / scale, part.Width, part.Height));
                    }
                }
                scene.Save(Path.Combine(folder, name));
            }
        }

        private void Finish(string failure)
        {
            if (finished) return;
            finished = true;
            foreach (Process process in shelves)
            {
                try
                {
                    if (process.HasExited) continue;
                    process.StandardInput.WriteLine("quit");
                    if (!process.WaitForExit(3000)) process.Kill();
                }
                catch (Exception) { }
            }
            List<string> lines = new List<string>(checks);
            lines.Insert(0, "Capri cross-platform smoke test on " + System.Runtime.InteropServices.RuntimeInformation.OSDescription);
            if (failure != null) lines.Add("FAIL " + failure);
            File.WriteAllLines(Path.Combine(folder, "smoke-test.txt"), lines);
            pet.Finish(failure == null ? 0 : 1);
        }
    }
}
