// Barre d'onglets dessinée à la manière de macOS : onglets de même largeur, titre centré, onglet actif plus
// clair, × à gauche au survol (ou clic du milieu), + à droite pour un nouvel onglet.
using System;
using System.Collections.Generic;
using System.Drawing;
using System.Windows.Forms;

namespace ArxivReader
{
    sealed class TabStrip : Control
    {
        static readonly Color Bar = Color.FromArgb(226, 224, 220);
        static readonly Color Active = Color.FromArgb(245, 243, 239);   // fond des pages de l'application
        static readonly Color Hover = Color.FromArgb(234, 232, 228);
        static readonly Color Line = Color.FromArgb(200, 198, 194);
        static readonly Color Ink = Color.FromArgb(34, 34, 34);
        static readonly Color Muted = Color.FromArgb(96, 96, 96);

        List<string> titles = new List<string>();
        int selected = -1, hover = -1;
        bool hoverClose, hoverAdd;

        public event Action<int> Selected;
        public event Action<int> Closed;
        public event Action Added;

        public TabStrip()
        {
            Dock = DockStyle.Top;
            DoubleBuffered = true;
            SetStyle(ControlStyles.ResizeRedraw | ControlStyles.OptimizedDoubleBuffer | ControlStyles.AllPaintingInWmPaint, true);
            Font = new Font("Segoe UI", 9f);
            Height = Scale(32);
        }

        float Ratio => DeviceDpi / 96f;
        int Scale(int pixels) => (int)Math.Round(pixels * Ratio);

        protected override void OnDpiChangedAfterParent(EventArgs e)
        {
            base.OnDpiChangedAfterParent(e);
            Height = Scale(32);
        }

        public void SetTabs(List<string> titles, int selected)
        {
            this.titles = titles;
            this.selected = selected;
            Invalidate();
        }

        Rectangle AddButton => new Rectangle(Width - Scale(36), 0, Scale(36), Height);

        Rectangle TabRect(int index)
        {
            int available = Width - AddButton.Width;
            int width = titles.Count == 0 ? 0 : available / titles.Count;
            return new Rectangle(index * width, 0, index == titles.Count - 1 ? available - index * width : width, Height);
        }

        Rectangle CloseRect(Rectangle tab)
        {
            int size = Scale(16);
            return new Rectangle(tab.Left + Scale(8), tab.Top + (tab.Height - size) / 2, size, size);
        }

        protected override void OnPaint(PaintEventArgs e)
        {
            var g = e.Graphics;
            g.Clear(Bar);
            g.SmoothingMode = System.Drawing.Drawing2D.SmoothingMode.AntiAlias;
            using var line = new Pen(Line, 1f);
            for (int i = 0; i < titles.Count; i++)
            {
                var tab = TabRect(i);
                if (i == selected)
                {
                    using var fill = new SolidBrush(Active);
                    g.FillRectangle(fill, tab);
                }
                else if (i == hover)
                {
                    using var fill = new SolidBrush(Hover);
                    g.FillRectangle(fill, tab);
                }
                if (i > 0 && i != selected && i - 1 != selected)
                    g.DrawLine(line, tab.Left, tab.Top + Scale(7), tab.Left, tab.Bottom - Scale(7));
                var text = Rectangle.Inflate(tab, -Scale(28), 0);
                TextRenderer.DrawText(g, titles[i], Font, text, i == selected ? Ink : Muted,
                    TextFormatFlags.HorizontalCenter | TextFormatFlags.VerticalCenter | TextFormatFlags.EndEllipsis |
                    TextFormatFlags.SingleLine | TextFormatFlags.NoPrefix);
                if (i == hover && titles.Count > 0)
                {
                    var close = CloseRect(tab);
                    if (hoverClose)
                    {
                        using var circle = new SolidBrush(Color.FromArgb(40, 0, 0, 0));
                        g.FillEllipse(circle, close);
                    }
                    using var cross = new Pen(Muted, Math.Max(1f, 1.3f * Ratio));
                    int m = Scale(5);
                    g.DrawLine(cross, close.Left + m, close.Top + m, close.Right - m, close.Bottom - m);
                    g.DrawLine(cross, close.Right - m, close.Top + m, close.Left + m, close.Bottom - m);
                }
            }
            if (selected < 0 || selected != titles.Count - 1)
                g.DrawLine(line, AddButton.Left, AddButton.Top + Scale(7), AddButton.Left, AddButton.Bottom - Scale(7));
            g.DrawLine(line, 0, Height - 1, Width, Height - 1);  // filet sous la barre, interrompu sous l'onglet actif
            if (selected >= 0)
            {
                using var under = new Pen(Active, 1f);
                var tab = TabRect(selected);
                g.DrawLine(under, tab.Left, Height - 1, tab.Right, Height - 1);
            }
            var plus = AddButton;
            if (hoverAdd)
            {
                using var fill = new SolidBrush(Hover);
                g.FillRectangle(fill, Rectangle.Inflate(plus, -Scale(4), -Scale(4)));
            }
            using var pen = new Pen(Muted, Math.Max(1f, 1.4f * Ratio));
            int cx = plus.Left + plus.Width / 2, cy = plus.Top + plus.Height / 2, r = Scale(6);
            g.DrawLine(pen, cx - r, cy, cx + r, cy);
            g.DrawLine(pen, cx, cy - r, cx, cy + r);
        }

        protected override void OnMouseMove(MouseEventArgs e)
        {
            base.OnMouseMove(e);
            int index = -1;
            for (int i = 0; i < titles.Count; i++) if (TabRect(i).Contains(e.Location)) index = i;
            bool close = index >= 0 && CloseRect(TabRect(index)).Contains(e.Location);
            bool add = AddButton.Contains(e.Location);
            if (index != hover || close != hoverClose || add != hoverAdd)
            {
                hover = index;
                hoverClose = close;
                hoverAdd = add;
                Invalidate();
            }
        }

        protected override void OnMouseLeave(EventArgs e)
        {
            base.OnMouseLeave(e);
            hover = -1;
            hoverClose = hoverAdd = false;
            Invalidate();
        }

        protected override void OnMouseUp(MouseEventArgs e)
        {
            base.OnMouseUp(e);
            if (AddButton.Contains(e.Location))
            {
                if (e.Button == MouseButtons.Left) Added?.Invoke();
                return;
            }
            for (int i = 0; i < titles.Count; i++)
            {
                var tab = TabRect(i);
                if (!tab.Contains(e.Location)) continue;
                if (e.Button == MouseButtons.Middle || (e.Button == MouseButtons.Left && CloseRect(tab).Contains(e.Location)))
                    Closed?.Invoke(i);
                else if (e.Button == MouseButtons.Left)
                    Selected?.Invoke(i);
                return;
            }
        }
    }
}
