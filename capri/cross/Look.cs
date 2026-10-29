using System;
using System.Collections.Generic;
using System.Globalization;
using Avalonia;
using Avalonia.Controls;
using Avalonia.Media;
using Avalonia.Platform;

namespace CapriDesktop
{
    internal static class Look
    {
        internal static readonly Color Panel = Color.FromRgb(9, 11, 15);
        internal static readonly Color Cyan = Color.FromRgb(58, 214, 235);
        internal static readonly Color Red = Color.FromRgb(232, 36, 64);
        internal static readonly Color Neon = Color.FromRgb(56, 176, 255);
        internal static readonly Color Ink = Color.FromRgb(232, 236, 240);
        internal static readonly Color Dim = Color.FromRgb(122, 128, 138);
        internal static readonly Color Edge = Color.FromRgb(70, 73, 82);
        internal static readonly Color Pill = Color.FromArgb(244, 20, 21, 25);
        internal static readonly FontFamily Mono = new FontFamily("avares://Capri/Assets#Share Tech Mono");
        internal static readonly Typeface Regular = new Typeface(Mono);
        internal static readonly Typeface Italic = new Typeface(Mono, FontStyle.Italic);
        internal static readonly Typeface Bold = new Typeface(Mono, FontStyle.Normal, FontWeight.Bold);

        internal static FormattedText Text(string text, Typeface face, double size, Color colour)
        {
            return new FormattedText(text, CultureInfo.InvariantCulture, FlowDirection.LeftToRight, face, size, new SolidColorBrush(colour));
        }

        internal static double CharWidth(double size)
        {
            return Text(new string('M', 20), Regular, size, Ink).WidthIncludingTrailingWhitespace / 20;
        }

        internal static Geometry Plate(Rect box, double cut, double tipX, double tipY, double tail, bool both = false)
        {
            double right = both ? cut : 0;
            List<Point> points = new List<Point>();
            double near = Math.Max(box.Left + cut + 2, tipX - tail * 0.8);
            double far = Math.Min(box.Right - right - 2, tipX + tail * 0.2);
            points.Add(new Point(box.Left + cut, box.Top));
            if (tipY < box.Top) { points.Add(new Point(near, box.Top)); points.Add(new Point(tipX, tipY)); points.Add(new Point(far, box.Top)); }
            points.Add(new Point(box.Right - right, box.Top));
            if (both) points.Add(new Point(box.Right, box.Top + cut));
            if (both) points.Add(new Point(box.Right, box.Bottom - cut));
            points.Add(new Point(box.Right - right, box.Bottom));
            if (tipY > box.Bottom) { points.Add(new Point(far, box.Bottom)); points.Add(new Point(tipX, tipY)); points.Add(new Point(near, box.Bottom)); }
            points.Add(new Point(box.Left + cut, box.Bottom));
            points.Add(new Point(box.Left, box.Bottom - cut));
            points.Add(new Point(box.Left, box.Top + cut));
            StreamGeometry geometry = new StreamGeometry();
            using (StreamGeometryContext context = geometry.Open())
            {
                context.BeginFigure(points[0], true);
                for (int i = 1; i < points.Count; i++) context.LineTo(points[i]);
                context.EndFigure(true);
            }
            return geometry;
        }

        internal static List<string> Wrap(string text, int columns)
        {
            List<string> lines = new List<string>();
            string line = "";
            foreach (string piece in (text ?? "").Split(' '))
            {
                string word = piece;
                while (word.Length > columns)
                {
                    if (line.Length > 0) { lines.Add(line); line = ""; }
                    lines.Add(word.Substring(0, columns));
                    word = word.Substring(columns);
                }
                if (line.Length == 0) line = word;
                else if (line.Length + 1 + word.Length <= columns) line += " " + word;
                else { lines.Add(line); line = word; }
            }
            if (line.Length > 0 || lines.Count == 0) lines.Add(line);
            return lines;
        }

        internal static void Overlay(Window window, string title)
        {
            window.Title = title;
            window.SystemDecorations = SystemDecorations.None;
            window.Background = Brushes.Transparent;
            window.TransparencyLevelHint = new[] { WindowTransparencyLevel.Transparent };
            window.Topmost = true;
            window.ShowInTaskbar = false;
            window.CanResize = false;
            window.ShowActivated = false;
            window.WindowStartupLocation = WindowStartupLocation.Manual;
        }
    }
}
