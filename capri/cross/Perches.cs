using System;
using System.Collections.Generic;
using Avalonia;

namespace CapriDesktop
{
    internal sealed class Ledge
    {
        internal long Window;
        internal PixelRect Bounds;
        internal bool Floor;
        internal bool Free;
        internal int Left;
        internal int Right;
        internal int Y;
    }

    internal static class Perches
    {
        internal static List<int[]> Open(int left, int right, int y, IEnumerable<PixelRect> above)
        {
            List<int[]> spans = new List<int[]> { new[] { left, right } };
            foreach (PixelRect cover in above)
            {
                if (y < cover.Y || y >= cover.Bottom) continue;
                List<int[]> next = new List<int[]>();
                foreach (int[] span in spans)
                {
                    if (cover.Right <= span[0] || cover.X >= span[1]) { next.Add(span); continue; }
                    if (cover.X > span[0]) next.Add(new[] { span[0], cover.X });
                    if (cover.Right < span[1]) next.Add(new[] { cover.Right, span[1] });
                }
                spans = next;
            }
            return spans;
        }

        internal static List<Ledge> Find(IEnumerable<Spot> windows, IEnumerable<PixelRect> floors)
        {
            List<Ledge> found = new List<Ledge>();
            List<PixelRect> above = new List<PixelRect>();
            foreach (Spot window in windows)
            {
                if (window.Seat)
                    foreach (int[] span in Open(window.Bounds.X, window.Bounds.Right, window.Bounds.Y, above))
                        found.Add(new Ledge { Window = window.Window, Bounds = window.Bounds, Left = span[0], Right = span[1], Y = window.Bounds.Y });
                above.Add(window.Bounds);
            }
            foreach (PixelRect floor in floors) found.Add(new Ledge { Floor = true, Left = floor.X, Right = floor.Right, Y = floor.Bottom });
            return found;
        }

        internal static Ledge Below(IEnumerable<Ledge> ledges, PixelPoint hip, int over, int reach, int slack, int room, Func<PixelPoint, PixelRect> screenAt)
        {
            Ledge best = null;
            foreach (Ledge ledge in ledges)
            {
                int drop = ledge.Y - hip.Y;
                if (drop < -over || drop > reach || ledge.Right - ledge.Left < 24) continue;
                if (hip.X < ledge.Left - slack || hip.X > ledge.Right + slack) continue;
                if (ledge.Y - room < screenAt(new PixelPoint(hip.X, ledge.Y)).Y) continue;
                if (best == null || Math.Abs(drop) < Math.Abs(best.Y - hip.Y)) best = ledge;
            }
            return best;
        }

        internal static bool Covered(IEnumerable<Spot> windows, Ledge seat, int x)
        {
            foreach (Spot window in windows)
            {
                if (window.Window == seat.Window) return false;
                if (window.Solid && x >= window.Bounds.X && x < window.Bounds.Right && seat.Y >= window.Bounds.Y && seat.Y < window.Bounds.Bottom) return true;
            }
            return false;
        }
    }
}
