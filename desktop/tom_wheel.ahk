#Requires AutoHotkey v2.0
#SingleInstance Force
#UseHook

; â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”
;  TOM Command Wheel  â€“  Win+Space radial menu
;  Communicates with TOM Brain via HTTP POST to 127.0.0.1:65401
; â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”

; â”€â”€ Configuration â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
global WHEEL_PORT  := 65401
global OUTER_R     := 140          ; action ring outer radius
global INNER_R     := 34           ; center dead-zone radius
global ICON_R      := 91           ; icon baseline radius
global WIN_HALF    := OUTER_R + 16 ; margin for soft edge lighting
global WIN_SIZE    := WIN_HALF * 2

; ARGB colors: alpha is composited by UpdateLayeredWindow.
global C_GLASS     := 0xB50A1421
global C_GLASS_ALT := 0x282D7191
global C_EDGE      := 0xA04CBDE0
global C_EDGE_SOFT := 0x3444BCE8
global C_ICON      := 0xE6D9F4FB
global C_ICON_DIM  := 0x786F91A0
global C_CENTER    := 0xE5091726
global C_CYAN      := 0xFF71E6FF
global C_BLUE      := 0xFF5CA9FF

; â”€â”€ Segment definitions (clockwise from 12 o'clock) â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
; GDI+ angles: 0=right, clockwise. 12 o'clock = -90Â° = 270Â°.
; Segment i: startAngle = -90 + i*45, sweep = 45
global Segments := [
    {icon: "ask",       action: "ask"},        ; top
    {icon: "unavailable", action: "ocr"},      ; upper-right, deferred
    {icon: "explain",   action: "explain"},    ; right
    {icon: "search",    action: "search"},     ; lower-right
    {icon: "note",      action: "note"},       ; bottom
    {icon: "remind",    action: "remind"},     ; lower-left
    {icon: "status",    action: "status"},     ; left
    {icon: "summarise", action: "summarise"},  ; upper-left
]

; â”€â”€ GDI+ global state â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
global pToken    := 0
global hGdiPlus  := 0
global g_wheel   := 0   ; current open WheelOverlay object (or 0)
global g_pending := Map()
global g_postSeq := 0

; â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”
;  Startup
; â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”
EnableDpiAwareness()
OnExit(_Cleanup)
pToken := GdiplusInit()
SetWinDelay(-1)
SetMouseDelay(-1)
CoordMode("Mouse", "Screen")

; â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”
;  Hotkey: Win+Space â†’ open wheel (suppress native layout switch)
; â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”
#Space:: {
    global g_wheel
    ; Win+Space toggles the overlay without stacking windows or timers.
    if (g_wheel != 0) {
        g_wheel.Close(true)
        g_wheel := 0
        return
    }
    MouseGetPos(&mx, &my)
    g_wheel := WheelOverlay(mx, my)
}

; â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”
;  WheelOverlay class
; â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”
class WheelOverlay {
    ; cx/cy  = clamped geometric centre of wheel (screen coordinates)
    ; wx/wy  = top-left of the overlay window
    ; hovered = 0-7 or -1

    __New(cx, cy) {
        this.hovered  := -1
        this.closed   := false
        this.closing  := false
        this.destroyed := false
        this.selecting := false
        this.selected := -1
        this.hoverAmount := 0.0
        this.openProgress := 0.0
        this.windowAlpha := 0
        this.renderScale := 0.93
        this.selectPulse := 0.0
        this.animState := "open"
        this.animStart := A_TickCount
        this.closeAction := ""

        this.scale := _MonitorScale(cx, cy)
        this.outerR := Round(OUTER_R * this.scale)
        this.innerR := Round(INNER_R * this.scale)
        this.iconR := Round(ICON_R * this.scale)
        this.winHalf := this.outerR + Round(16 * this.scale)
        this.winSize := this.winHalf * 2

        ; Clamp to the virtual desktop, including negative-coordinate monitors.
        vLeft  := SysGet(76)   ; SM_XVIRTUALSCREEN
        vTop   := SysGet(77)   ; SM_YVIRTUALSCREEN
        vW     := SysGet(78)   ; SM_CXVIRTUALSCREEN
        vH     := SysGet(79)   ; SM_CYVIRTUALSCREEN
        this.wx := Max(vLeft, Min(cx - this.winHalf, vLeft + vW - this.winSize))
        this.wy := Max(vTop,  Min(cy - this.winHalf, vTop  + vH - this.winSize))
        this.cx := this.wx + this.winHalf
        this.cy := this.wy + this.winHalf

        ; Create layered tool-window (no taskbar, no title, transparent)
        this.gui := Gui("+AlwaysOnTop -Caption +ToolWindow +E0x80000")  ; E0x80000 = WS_EX_LAYERED
        this.gui.BackColor := "000000"
        this.gui.Show("x" this.wx " y" this.wy " w" this.winSize " h" this.winSize " NoActivate")
        this.hwnd := this.gui.Hwnd

        ; Make the window click-through initially (WS_EX_TRANSPARENT), then remove it
        ; once the user interacts â€” this avoids stealing focus.
        ; Actually we keep it NOT transparent so mouse events arrive.
        exStyle := WinGetExStyle(this.hwnd)
        WinSetExStyle(exStyle & ~0x20, this.hwnd)   ; clear WS_EX_TRANSPARENT

        ; Draw the first translucent animation frame.
        try this.DrawPremium(-1)
        catch as err {
            this.gui.Destroy()
            throw err
        }

        ; Install message hooks. Keep the bound objects so removal targets
        ; the exact same callbacks (otherwise handlers accumulate per open).
        this.hMouseMove := ObjBindMethod(this, "_OnMouseMove")
        this.hLButton   := ObjBindMethod(this, "_OnLButton")
        this.hRButton   := ObjBindMethod(this, "_OnRButton")
        this.hEscape    := ObjBindMethod(this, "_Cancel")
        OnMessage(0x0200, this.hMouseMove)  ; WM_MOUSEMOVE
        OnMessage(0x0201, this.hLButton)    ; WM_LBUTTONDOWN
        OnMessage(0x0204, this.hRButton)    ; WM_RBUTTONDOWN

        ; Escape key to cancel (only enabled while the wheel exists)
        Hotkey("Escape", this.hEscape, "On")

        ; One lightweight timer drives hover and short entry/exit animations.
        this.leftDown := GetKeyState("LButton", "P")
        this.rightDown := GetKeyState("RButton", "P")
        this.timer := ObjBindMethod(this, "_Poll")
        SetTimer(this.timer, 16)
    }

    ; â”€â”€ Drawing â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
    DrawPremium(hovered) {
        global pToken
        if (!pToken)
            throw Error("GDI+ is not initialized; refusing to render the wheel.")

        w := this.winSize, h := this.winSize
        cx := this.winHalf, cy := this.winHalf
        pBmp := 0, pGfx := 0, pBr := 0, pPen := 0, pIconPen := 0, pMatrix := 0
        hBmp := 0, hDC := 0, hMemDC := 0, hOld := 0
        cleanupErrors := []
        try {
            status := DllCall("gdiplus\GdipCreateBitmapFromScan0", "Int", w, "Int", h,
                "Int", 0, "Int", 0x26200A, "Ptr", 0, "Ptr*", &pBmp, "UInt")
            GdipRequire("GdipCreateBitmapFromScan0", status, pBmp, true)
            status := DllCall("gdiplus\GdipGetImageGraphicsContext", "Ptr", pBmp,
                "Ptr*", &pGfx, "UInt")
            GdipRequire("GdipGetImageGraphicsContext", status, pGfx, true)
            GdipRequire("GdipGraphicsClear",
                DllCall("gdiplus\GdipGraphicsClear", "Ptr", pGfx, "UInt", 0, "UInt"), pGfx)

            scale := this.renderScale
            dx := cx * (1 - scale), dy := cy * (1 - scale)
            status := DllCall("gdiplus\GdipCreateMatrix2", "Float", scale, "Float", 0,
                "Float", 0, "Float", scale, "Float", dx, "Float", dy,
                "Ptr*", &pMatrix, "UInt")
            GdipRequire("GdipCreateMatrix2", status, pMatrix, true)
            GdipRequire("GdipSetWorldTransform",
                DllCall("gdiplus\GdipSetWorldTransform", "Ptr", pGfx, "Ptr", pMatrix, "UInt"), pMatrix)

            ; Low-alpha rings create the glass edge glow and soft outer shadow.
            Loop 5 {
                ring := Round((6 - A_Index) * this.scale)
                alpha := Round((6 - A_Index) * 5)
                color := (alpha << 24) | 0x174D72
                pBr := GdipCreateBrush(color)
                GdipRequire("GdipFillEllipse", DllCall("gdiplus\GdipFillEllipse", "Ptr", pGfx,
                    "Ptr", pBr, "Float", cx - this.outerR - ring, "Float", cy - this.outerR - ring,
                    "Float", (this.outerR + ring) * 2, "Float", (this.outerR + ring) * 2, "UInt"), pBr)
                GdipDeleteChecked("GdipDeleteBrush", &pBr)
            }
            this._FillEllipse(pGfx, C_GLASS, cx - this.outerR, cy - this.outerR,
                this.outerR * 2, this.outerR * 2)
            this._FillPie(pGfx, 0x182F7991, cx - this.outerR + 3*this.scale, cy - this.outerR + 3*this.scale,
                (this.outerR - 3*this.scale) * 2, (this.outerR - 3*this.scale) * 2, 208, 124)
            this._FillEllipse(pGfx, 0x162B5166, cx - this.outerR + 8*this.scale, cy - this.outerR + 8*this.scale,
                (this.outerR - 8*this.scale) * 2, (this.outerR - 8*this.scale) * 2)

            ; Sector glass tint and animated cyan hover glow.
            Loop 8 {
                i := A_Index - 1
                entry := Min(1.0, Max(0.0, (this.openProgress - i * 0.035) / 0.55))
                if (entry <= 0)
                    continue
                startAngle := -90 + i * 45 + 0.6
                strength := (i == hovered) ? this.hoverAmount : 0.0
                if (this.selected == i)
                    strength := Max(strength, this.selectPulse)
                baseAlpha := Round(18 * entry)
                baseColor := (baseAlpha << 24) | 0x236580
                this._FillPie(pGfx, baseColor, cx - this.outerR + 2*this.scale, cy - this.outerR + 2*this.scale,
                    (this.outerR - 2*this.scale) * 2, (this.outerR - 2*this.scale) * 2, startAngle, 43.8)
                if (strength > 0.01 && Segments[A_Index].icon != "unavailable") {
                    glowAlpha := Round((18 + 42 * strength) * entry)
                    glowColor := (glowAlpha << 24) | 0x168FCE
                    this._FillPie(pGfx, glowColor, cx - this.outerR + 2*this.scale, cy - this.outerR + 2*this.scale,
                        (this.outerR - 2*this.scale) * 2, (this.outerR - 2*this.scale) * 2, startAngle, 43.8)
                }
            }

            ; Thin separators, inset from the glass boundary.
            pPen := GdipCreatePen(C_EDGE, 1.15 * this.scale)
            GdipRequire("GdipSetPenLineCap197819", DllCall("gdiplus\GdipSetPenLineCap197819",
                "Ptr", pPen, "Int", 2, "Int", 2, "Int", 2, "UInt"), pPen)
            Loop 8 {
                angle := (-90 + (A_Index - 1) * 45) * 3.14159265 / 180
                x1 := cx + (this.innerR + 5*this.scale) * Cos(angle), y1 := cy + (this.innerR + 5*this.scale) * Sin(angle)
                x2 := cx + (this.outerR - 8*this.scale) * Cos(angle), y2 := cy + (this.outerR - 8*this.scale) * Sin(angle)
                GdipRequire("GdipDrawLine", DllCall("gdiplus\GdipDrawLine", "Ptr", pGfx,
                    "Ptr", pPen, "Float", x1, "Float", y1, "Float", x2, "Float", y2, "UInt"), pPen)
            }
            GdipDeleteChecked("GdipDeletePen", &pPen)

            ; Refined outer and inner glass rims.
            this._StrokeEllipse(pGfx, 0xB04FBBD9, 1.2*this.scale, cx - this.outerR, cy - this.outerR,
                this.outerR * 2, this.outerR * 2)
            this._StrokeEllipse(pGfx, 0x604EADC7, 1.0*this.scale, cx - this.innerR, cy - this.innerR,
                this.innerR * 2, this.innerR * 2)
            this._FillEllipse(pGfx, C_CENTER, cx - this.innerR + 1*this.scale, cy - this.innerR + 1*this.scale,
                (this.innerR - 1*this.scale) * 2, (this.innerR - 1*this.scale) * 2)
            this._StrokeEllipse(pGfx, 0x9B55C8E8, 1.0*this.scale, cx - this.innerR + 3*this.scale,
                cy - this.innerR + 3*this.scale, (this.innerR - 3*this.scale) * 2,
                (this.innerR - 3*this.scale) * 2)
            this._DrawTomMark(pGfx, cx, cy, this.scale)

            ; Simple, consistent vector icons; the deferred OCR eye is dimmed and crossed out.
            Loop 8 {
                i := A_Index - 1
                entry := Min(1.0, Max(0.0, (this.openProgress - i * 0.035) / 0.55))
                if (entry <= 0)
                    continue
                mid := (-90 + i * 45 + 22.5) * 3.14159265 / 180
                strength := (i == hovered) ? this.hoverAmount : 0.0
                if (this.selected == i)
                    strength := Max(strength, this.selectPulse)
                radius := this.iconR + 2*this.scale * strength
                iconX := cx + radius * Cos(mid), iconY := cy + radius * Sin(mid)
                iconSize := 25 * this.scale * (1 + 0.08 * strength)
                iconColor := Segments[A_Index].icon == "unavailable" ? C_ICON_DIM : C_ICON
                iconAlpha := Round(((iconColor >> 24) & 0xFF) * entry)
                iconColor := (iconAlpha << 24) | (iconColor & 0xFFFFFF)
                this._DrawIcon(pGfx, Segments[A_Index].icon, iconX, iconY, iconSize, iconColor)
            }

            status := DllCall("gdiplus\GdipCreateHBITMAPFromBitmap", "Ptr", pBmp,
                "Ptr*", &hBmp, "UInt", 0, "UInt")
            GdipRequire("GdipCreateHBITMAPFromBitmap", status, hBmp, true)
            hDC := DllCall("GetDC", "Ptr", 0, "Ptr")
            if (!hDC)
                throw Error("GetDC failed while rendering the wheel.")
            hMemDC := DllCall("CreateCompatibleDC", "Ptr", hDC, "Ptr")
            if (!hMemDC)
                throw Error("CreateCompatibleDC failed while rendering the wheel.")
            hOld := DllCall("SelectObject", "Ptr", hMemDC, "Ptr", hBmp, "Ptr")
            if (!hOld || hOld == -1)
                throw Error("SelectObject failed while rendering the wheel.")

            POINT_src := Buffer(8, 0), SIZE_win := Buffer(8, 0), POINT_dst := Buffer(8, 0)
            NumPut("Int", w, SIZE_win, 0), NumPut("Int", h, SIZE_win, 4)
            NumPut("Int", this.wx, POINT_dst, 0), NumPut("Int", this.wy, POINT_dst, 4)
            BLENDFUNCTION := Buffer(4, 0)
            NumPut("UChar", 0, BLENDFUNCTION, 0), NumPut("UChar", 0, BLENDFUNCTION, 1)
            NumPut("UChar", Round(255 * this.windowAlpha), BLENDFUNCTION, 2)
            NumPut("UChar", 1, BLENDFUNCTION, 3)
            DllCall("SetLastError", "UInt", 0)
            if (!DllCall("UpdateLayeredWindow", "Ptr", this.hwnd, "Ptr", hDC,
                "Ptr", POINT_dst, "Ptr", SIZE_win, "Ptr", hMemDC, "Ptr", POINT_src,
                "UInt", 0, "Ptr", BLENDFUNCTION, "Int", 2)) {
                winError := DllCall("GetLastError", "UInt")
                bmpType := DllCall("GetObjectType", "Ptr", hBmp, "UInt")
                throw Error("UpdateLayeredWindow failed: win32=" winError
                    ", bitmap=" Format("0x{:X}", hBmp) ", bitmapType=" bmpType
                    ", window=" Format("0x{:X}", this.hwnd)
                    ", destination=" this.wx "," this.wy ", size=" w "," h)
            }
        } finally {
            if (hOld && hOld != -1 && hMemDC)
                DllCall("SelectObject", "Ptr", hMemDC, "Ptr", hOld, "Ptr")
            if (hMemDC)
                DllCall("DeleteDC", "Ptr", hMemDC)
            if (hDC)
                DllCall("ReleaseDC", "Ptr", 0, "Ptr", hDC, "Int")
            if (hBmp)
                DllCall("DeleteObject", "Ptr", hBmp, "Int")
            if (pBr)
                try GdipDeleteChecked("GdipDeleteBrush", &pBr)
                catch as err
                    cleanupErrors.Push(err.Message)
            if (pPen)
                try GdipDeleteChecked("GdipDeletePen", &pPen)
                catch as err
                    cleanupErrors.Push(err.Message)
            if (pIconPen)
                try GdipDeleteChecked("GdipDeletePen", &pIconPen)
                catch as err
                    cleanupErrors.Push(err.Message)
            if (pMatrix)
                try GdipDeleteChecked("GdipDeleteMatrix", &pMatrix)
                catch as err
                    cleanupErrors.Push(err.Message)
            if (pGfx)
                try GdipDeleteChecked("GdipDeleteGraphics", &pGfx)
                catch as err
                    cleanupErrors.Push(err.Message)
            if (pBmp)
                try GdipDeleteChecked("GdipDisposeImage", &pBmp)
                catch as err
                    cleanupErrors.Push(err.Message)
            if (cleanupErrors.Length) {
                cleanupText := ""
                for errorText in cleanupErrors
                    cleanupText .= (cleanupText == "" ? "" : "`n") . errorText
                throw Error("GDI+ cleanup failure:`n" . cleanupText)
            }
        }
    }

    _FillEllipse(gfx, color, x, y, w, h) {
        brush := 0
        try {
            status := DllCall("gdiplus\GdipCreateSolidFill", "UInt", color, "Ptr*", &brush, "UInt")
            GdipRequire("GdipCreateSolidFill", status, brush, true)
            GdipRequire("GdipFillEllipse", DllCall("gdiplus\GdipFillEllipse", "Ptr", gfx,
                "Ptr", brush, "Float", x, "Float", y, "Float", w, "Float", h, "UInt"), brush)
        } finally {
            if (brush)
                GdipDeleteChecked("GdipDeleteBrush", &brush)
        }
    }

    _FillPie(gfx, color, x, y, w, h, start, sweep) {
        brush := 0
        try {
            status := DllCall("gdiplus\GdipCreateSolidFill", "UInt", color, "Ptr*", &brush, "UInt")
            GdipRequire("GdipCreateSolidFill", status, brush, true)
            GdipRequire("GdipFillPie", DllCall("gdiplus\GdipFillPie", "Ptr", gfx,
                "Ptr", brush, "Float", x, "Float", y, "Float", w, "Float", h,
                "Float", start, "Float", sweep, "UInt"), brush)
        } finally {
            if (brush)
                GdipDeleteChecked("GdipDeleteBrush", &brush)
        }
    }

    _StrokeEllipse(gfx, color, width, x, y, w, h) {
        pen := 0
        try {
            status := DllCall("gdiplus\GdipCreatePen1", "UInt", color, "Float", width,
                "Int", 2, "Ptr*", &pen, "UInt")
            GdipRequire("GdipCreatePen1", status, pen, true)
            GdipRequire("GdipDrawEllipse", DllCall("gdiplus\GdipDrawEllipse", "Ptr", gfx,
                "Ptr", pen, "Float", x, "Float", y, "Float", w, "Float", h, "UInt"), pen)
        } finally {
            if (pen)
                GdipDeleteChecked("GdipDeletePen", &pen)
        }
    }

    _DrawTomMark(gfx, cx, cy, scale) {
        pen := 0
        try {
            status := DllCall("gdiplus\GdipCreatePen1", "UInt", 0xFF8AEAFF, "Float", 2.4*scale,
                "Int", 2, "Ptr*", &pen, "UInt")
            GdipRequire("GdipCreatePen1", status, pen, true)
            GdipRequire("GdipSetPenLineCap197819", DllCall("gdiplus\GdipSetPenLineCap197819",
                "Ptr", pen, "Int", 2, "Int", 2, "Int", 2, "UInt"), pen)
            this._IconLine(gfx, pen, cx - 10*scale, cy - 6*scale, cx + 10*scale, cy - 6*scale)
            this._IconLine(gfx, pen, cx, cy - 6*scale, cx, cy + 9*scale)
            this._IconLine(gfx, pen, cx - 5*scale, cy + 9*scale, cx + 5*scale, cy + 9*scale)
            this._FillEllipse(gfx, 0xFFD7FAFF, cx - 1.3*scale, cy - 1.3*scale, 2.6*scale, 2.6*scale)
        } finally {
            if (pen)
                GdipDeleteChecked("GdipDeletePen", &pen)
        }
    }

    _DrawIcon(gfx, icon, x, y, size, color) {
        pen := 0
        try {
            status := DllCall("gdiplus\GdipCreatePen1", "UInt", color, "Float", 2.25,
                "Int", 2, "Ptr*", &pen, "UInt")
            GdipRequire("GdipCreatePen1", status, pen, true)
            GdipRequire("GdipSetPenLineCap197819", DllCall("gdiplus\GdipSetPenLineCap197819",
                "Ptr", pen, "Int", 2, "Int", 2, "Int", 2, "UInt"), pen)
            u := size / 24
            switch icon {
                case "ask":
                    this._IconEllipse(gfx, pen, x - 9*u, y - 7*u, 18*u, 14*u)
                    this._IconLine(gfx, pen, x - 4*u, y + 6*u, x - 8*u, y + 10*u)
                    this._IconLine(gfx, pen, x - 8*u, y + 10*u, x - 7*u, y + 4*u)
                    this._IconEllipse(gfx, pen, x - 4*u, y - 1*u, 1.5*u, 1.5*u)
                    this._IconEllipse(gfx, pen, x - 0.75*u, y - 1*u, 1.5*u, 1.5*u)
                    this._IconEllipse(gfx, pen, x + 2.5*u, y - 1*u, 1.5*u, 1.5*u)
                case "unavailable":
                    this._IconEllipse(gfx, pen, x - 10*u, y - 6*u, 20*u, 12*u)
                    this._IconEllipse(gfx, pen, x - 2.5*u, y - 2.5*u, 5*u, 5*u)
                    this._IconLine(gfx, pen, x - 10*u, y + 10*u, x + 10*u, y - 10*u)
                case "explain":
                    this._IconEllipse(gfx, pen, x - 7*u, y - 9*u, 14*u, 14*u)
                    this._IconLine(gfx, pen, x - 4*u, y + 6*u, x + 4*u, y + 6*u)
                    this._IconLine(gfx, pen, x - 3*u, y + 9*u, x + 3*u, y + 9*u)
                    this._IconLine(gfx, pen, x, y - 13*u, x, y - 11*u)
                    this._IconLine(gfx, pen, x - 12*u, y - 7*u, x - 10*u, y - 6*u)
                    this._IconLine(gfx, pen, x + 12*u, y - 7*u, x + 10*u, y - 6*u)
                case "search":
                    this._IconEllipse(gfx, pen, x - 9*u, y - 10*u, 14*u, 14*u)
                    this._IconLine(gfx, pen, x + 3*u, y + 2*u, x + 10*u, y + 9*u)
                case "note":
                    this._IconLine(gfx, pen, x - 7*u, y - 10*u, x + 3*u, y - 10*u)
                    this._IconLine(gfx, pen, x + 3*u, y - 10*u, x + 7*u, y - 6*u)
                    this._IconLine(gfx, pen, x + 7*u, y - 6*u, x + 7*u, y + 10*u)
                    this._IconLine(gfx, pen, x + 7*u, y + 10*u, x - 7*u, y + 10*u)
                    this._IconLine(gfx, pen, x - 7*u, y + 10*u, x - 7*u, y - 10*u)
                    this._IconLine(gfx, pen, x + 3*u, y - 10*u, x + 3*u, y - 6*u)
                    this._IconLine(gfx, pen, x + 3*u, y - 6*u, x + 7*u, y - 6*u)
                    this._IconLine(gfx, pen, x - 4*u, y - 1*u, x + 4*u, y - 1*u)
                    this._IconLine(gfx, pen, x - 4*u, y + 3*u, x + 4*u, y + 3*u)
                case "remind":
                    this._IconArc(gfx, pen, x - 8*u, y - 8*u, 16*u, 17*u, 200, 140)
                    this._IconLine(gfx, pen, x - 7*u, y + 2*u, x - 7*u, y + 6*u)
                    this._IconLine(gfx, pen, x + 7*u, y + 2*u, x + 7*u, y + 6*u)
                    this._IconLine(gfx, pen, x - 9*u, y + 6*u, x + 9*u, y + 6*u)
                    this._IconEllipse(gfx, pen, x - 1.5*u, y + 8*u, 3*u, 3*u)
                case "status":
                    this._IconLine(gfx, pen, x - 10*u, y + 9*u, x + 10*u, y + 9*u)
                    this._IconLine(gfx, pen, x - 8*u, y + 7*u, x - 8*u, y + 2*u)
                    this._IconLine(gfx, pen, x - 2*u, y + 7*u, x - 2*u, y - 3*u)
                    this._IconLine(gfx, pen, x + 4*u, y + 7*u, x + 4*u, y - 8*u)
                    this._IconLine(gfx, pen, x + 10*u, y + 7*u, x + 10*u, y - 1*u)
                case "summarise":
                    this._IconLine(gfx, pen, x - 8*u, y - 5*u, x - 8*u, y - 10*u)
                    this._IconLine(gfx, pen, x - 8*u, y - 10*u, x + 3*u, y - 10*u)
                    this._IconLine(gfx, pen, x + 3*u, y - 10*u, x + 7*u, y - 6*u)
                    this._IconLine(gfx, pen, x + 7*u, y - 6*u, x + 7*u, y + 7*u)
                    this._IconLine(gfx, pen, x + 7*u, y + 7*u, x + 4*u, y + 10*u)
                    this._IconLine(gfx, pen, x + 4*u, y + 10*u, x - 8*u, y + 10*u)
                    this._IconLine(gfx, pen, x - 8*u, y + 10*u, x - 8*u, y - 5*u)
                    this._IconLine(gfx, pen, x + 3*u, y - 10*u, x + 3*u, y - 6*u)
                    this._IconLine(gfx, pen, x + 3*u, y - 6*u, x + 7*u, y - 6*u)
                    this._IconLine(gfx, pen, x - 4*u, y - 1*u, x + 3*u, y - 1*u)
                    this._IconLine(gfx, pen, x - 4*u, y + 3*u, x + 3*u, y + 3*u)
            }
        } finally {
            if (pen)
                GdipDeleteChecked("GdipDeletePen", &pen)
        }
    }

    _IconLine(gfx, pen, x1, y1, x2, y2) {
        GdipRequire("GdipDrawLine", DllCall("gdiplus\GdipDrawLine", "Ptr", gfx,
            "Ptr", pen, "Float", x1, "Float", y1, "Float", x2, "Float", y2, "UInt"), pen)
    }

    _IconEllipse(gfx, pen, x, y, w, h) {
        GdipRequire("GdipDrawEllipse", DllCall("gdiplus\GdipDrawEllipse", "Ptr", gfx,
            "Ptr", pen, "Float", x, "Float", y, "Float", w, "Float", h, "UInt"), pen)
    }

    _IconArc(gfx, pen, x, y, w, h, start, sweep) {
        GdipRequire("GdipDrawArc", DllCall("gdiplus\GdipDrawArc", "Ptr", gfx,
            "Ptr", pen, "Float", x, "Float", y, "Float", w, "Float", h,
            "Float", start, "Float", sweep, "UInt"), pen)
    }

    GetSegment(mx, my) {
        dx := mx - this.cx
        dy := my - this.cy
        dist := Sqrt(dx*dx + dy*dy)
        if (dist < this.innerR || dist > this.outerR)
            return -1
        ; atan2 â†’ degrees, normalise so 0 = top/12 o'clock, clockwise
        ang := ArcTan(dy, dx) * 180 / 3.14159265   ; -180..180, 0=right
        ang := Mod(ang + 90 + 360, 360)              ; 0=top, clockwise
        return Floor(ang / 45)
    }

    ; â”€â”€ Poll timer â€“ runs every 20 ms â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
    _Poll(*) {
        if (this.destroyed)
            return

        now := A_TickCount
        redraw := false
        if (this.animState == "open") {
            t := Min(1.0, (now - this.animStart) / 170.0)
            eased := 1 - (1 - t) ** 3
            this.openProgress := eased
            this.windowAlpha := eased
            this.renderScale := 0.925 + 0.075 * eased
            redraw := true
            if (t >= 1)
                this.animState := "idle"
        }

        if (this.selecting) {
            pulseT := Min(1.0, (now - this.selectStart) / 115.0)
            this.selectPulse := Sin(pulseT * 3.14159265)
            redraw := true
            if (pulseT >= 1) {
                this.selecting := false
                this.closing := true
                this.animState := "close"
                this.closeStart := now
                this.closeAlphaStart := this.windowAlpha
                this.closeScaleStart := this.renderScale
                this.closeAction := this.pendingAction
            }
        } else if (this.closing) {
            closeT := Min(1.0, (now - this.closeStart) / 125.0)
            this.windowAlpha := this.closeAlphaStart * (1 - closeT)
            this.renderScale := this.closeScaleStart * (1 - 0.045 * closeT)
            redraw := true
            if (closeT >= 1) {
                action := this.closeAction
                this._Destroy()
                if (action != "")
                    this._Dispatch(action)
                return
            }
        } else {
            MouseGetPos(&mx, &my)
            leftNow := GetKeyState("LButton", "P")
            rightNow := GetKeyState("RButton", "P")
            if ((leftNow && !this.leftDown) || (rightNow && !this.rightDown)) {
                if (mx < this.wx || mx >= this.wx + this.winSize || my < this.wy || my >= this.wy + this.winSize) {
                    this.Close()
                    return
                }
            }
            this.leftDown := leftNow
            this.rightDown := rightNow
            seg := this.GetSegment(mx, my)
            if (seg != this.hovered) {
                this.hovered := seg
                redraw := true
            }
            target := (this.hovered >= 0 && Segments[this.hovered + 1].icon != "unavailable") ? 1.0 : 0.0
            nextAmount := this.hoverAmount + (target - this.hoverAmount) * 0.34
            if (Abs(target - nextAmount) < 0.025)
                nextAmount := target
            if (Abs(nextAmount - this.hoverAmount) > 0.002) {
                this.hoverAmount := nextAmount
                redraw := true
            }
        }

        if (redraw)
            this.DrawPremium(this.hovered)
    }

    ; â”€â”€ Mouse message handlers â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
    _OnMouseMove(wParam, lParam, msg, hwnd) {
        if (hwnd != this.hwnd || this.closed || this.closing)
            return
        mx := this.wx + (lParam & 0xFFFF)
        my := this.wy + ((lParam >> 16) & 0xFFFF)
        seg := this.GetSegment(mx, my)
        this.hovered := seg
    }

    _OnLButton(wParam, lParam, msg, hwnd) {
        if (this.closed || this.closing)
            return
        ; A click in another window dismisses the wheel but must not dispatch.
        if (hwnd != this.hwnd) {
            this.Close()
            return
        }
        MouseGetPos(&mx, &my)
        seg := this.GetSegment(mx, my)
        if (seg >= 0) {
            this.selected := seg
            this.pendingAction := Segments[seg + 1].action
            this.selectStart := A_TickCount
            this.selectPulse := 0.0
            this.selecting := true
        } else
            this.Close()
    }

    _OnRButton(wParam, lParam, msg, hwnd) {
        if (this.closed || this.closing)
            return
        this.Close()
    }

    _Cancel(*) {
        if (!this.closed && !this.closing)
            this.Close()
    }

    ; â”€â”€ Dispatch action to TOM â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
    _Dispatch(action) {
        ctx := ""
        approved := false

        if (action == "ocr") {
            _Notify("TOM", "OCR is deferred and unavailable.", 3)
            return
        }

        if (action == "summarise") {
            ; Try clipboard for a URL first (simple heuristic)
            clip := A_Clipboard
            if (RegExMatch(clip, "^https?://\S+$"))
                ctx := clip
            else {
                ; Ask user to paste URL
                ctx := InputBox("Enter URL to summarise:", "TOM â€“ Summarise", "w400 h100").Value
                if (ctx == "")
                    return
            }
        } else {
            ; For all other actions: grab selected text via Ctrl+C with clipboard restore
            ctx := this._GetSelectedText()
        }

        if (action == "note" && Trim(ctx) == "") {
            answer := InputBox("Enter the note text to save:", "TOM - Note", "w480 h160")
            if (answer.Result != "OK" || Trim(answer.Value) == "")
                return
            ctx := answer.Value
        } else if (action == "remind" && Trim(ctx) == "") {
            answer := InputBox("What should TOM remind you about, and when?", "TOM - Remind", "w480 h160")
            if (answer.Result != "OK" || Trim(answer.Value) == "")
                return
            ctx := answer.Value
        }

        if (action == "note" || action == "remind") {
            verb := action == "note" ? "save the note" : "create the reminder"
            choice := MsgBox("Allow TOM to " verb "?`n`n" SubStr(ctx, 1, 500), "TOM confirmation", "YesNo Icon!")
            approved := (choice == "Yes")
        }

        this._PostAsync(action, ctx, "", approved)
    }

    ; â”€â”€ Grab selected text, restoring clipboard â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
    _GetSelectedText() {
        saved := ClipboardAll()          ; save full clipboard
        txt := ""
        try {
            A_Clipboard := ""
            Send("^c")
            if ClipWait(0.4, 1)           ; wait up to 400ms for text
                txt := A_Clipboard
        } catch {
            txt := ""
        } finally {
            A_Clipboard := saved
        }
        return Trim(txt)
    }

    ; â”€â”€ OCR region drag selector â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
    ; Returns "[x1,y1,x2,y2]" JSON array string, or "" on cancel
    _SelectRegion() {
        ; Create a full-virtual-screen transparent overlay for drag selection
        vLeft := SysGet(76), vTop := SysGet(77)
        vW    := SysGet(78), vH   := SysGet(79)

        selGui := Gui("+AlwaysOnTop -Caption +ToolWindow +E0x80000")
        selGui.BackColor := "000000"
        selGui.Show("x" vLeft " y" vTop " w" vW " h" vH " NoActivate")
        WinSetTransparent(40, selGui)   ; very subtle overlay
        selHwnd := selGui.Hwnd

        ; Track drag
        x1 := 0, y1 := 0, x2 := 0, y2 := 0, dragging := false, done := false

        loop {
            GetPhysicalCursorPos := DllCall("GetPhysicalCursorPos", "Ptr", pos := Buffer(8))
            mx := NumGet(pos, 0, "Int"), my := NumGet(pos, 4, "Int")

            if (GetKeyState("LButton", "P")) {
                if (!dragging) {
                    x1 := mx, y1 := my
                    dragging := true
                }
                x2 := mx, y2 := my
            } else if (dragging) {
                done := true
                break
            }
            if (GetKeyState("Escape", "P") || GetKeyState("RButton", "P")) {
                break
            }
            Sleep(16)
        }

        selGui.Destroy()

        if (!done || Abs(x2-x1) < 5 || Abs(y2-y1) < 5)
            return ""

        lx := Min(x1,x2), ty := Min(y1,y2)
        rx := Max(x1,x2), by := Max(y1,y2)
        return "[" lx "," ty "," rx "," by "]"
    }

    ; â”€â”€ Fire-and-forget HTTP POST â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
    ; Spawns curl.exe and returns immediately; a global poll timer
    ; consumes the response file without blocking the AHK event loop.
    _PostAsync(action, context, region, approved := false) {
        _StartPost(action, context, region, approved)
    }

    ; â”€â”€ Cleanup â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
    Close(immediate := false) {
        if (this.destroyed)
            return
        if (immediate) {
            this._Destroy()
            return
        }
        if (this.closing)
            return
        this.closing := true
        this.animState := "close"
        this.closeStart := A_TickCount
        this.closeAlphaStart := this.windowAlpha
        this.closeScaleStart := this.renderScale
    }

    _Destroy() {
        if (this.destroyed)
            return
        this.destroyed := true
        this.closed := true
        SetTimer(this.timer, 0)
        OnMessage(0x0200, this.hMouseMove, 0)
        OnMessage(0x0201, this.hLButton,   0)
        OnMessage(0x0204, this.hRButton,   0)
        try Hotkey("Escape", this.hEscape, "Off")
        this.gui.Destroy()
        global g_wheel
        if (g_wheel == this)
            g_wheel := 0
    }

    __Delete() {
        if (!this.destroyed)
            this.Close(true)
    }
}

; â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”
;  HTTP dispatch (non-blocking)
; â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”
_StartPost(action, context, region, approved := false) {
    global g_pending, g_postSeq

    g_postSeq += 1
    rid := "wheel-" DllCall("GetCurrentProcessId") "-" A_TickCount "-" g_postSeq
    reqFile  := A_Temp "\tom_wheel_" rid ".req"
    tmpFile  := A_Temp "\tom_wheel_" rid ".tmp"
    respFile := A_Temp "\tom_wheel_" rid ".resp"

    body := _BuildBody(action, context, region, rid, approved)
    try {
        f := FileOpen(reqFile, "w", "UTF-8-RAW")
        if !f
            throw Error("could not open request file")
        f.Write(body)
        f.Close()
    } catch as err {
        _Notify("TOM", "Could not prepare request.", 3)
        return
    }

    ; curl writes to a temp file, then cmd moves it into place. The move is
    ; the completion marker, so the poll timer never reads a partial body.
    cmdLine := A_ComSpec ' /c curl.exe --noproxy "*" --silent --show-error'
    cmdLine .= ' --max-time 65 --connect-timeout 5'
    cmdLine .= ' -H "Content-Type: application/json"'
    cmdLine .= ' --data-binary "@' reqFile '" --output "' tmpFile '"'
    cmdLine .= ' "http://127.0.0.1:' WHEEL_PORT '/wheel"'
    cmdLine .= ' & move /y "' tmpFile '" "' respFile '" >nul 2>nul'
    cmdLine .= ' & if not exist "' respFile '" type nul > "' respFile '"'
    cmdLine .= ' & del "' tmpFile '" >nul 2>nul'

    try Run(cmdLine, , "Hide")
    catch as err {
        try FileDelete(reqFile)
        _Notify("TOM", "Could not start HTTP helper.", 3)
        return
    }

    g_pending[rid] := {
        action: action,
        reqFile: reqFile,
        respFile: respFile,
        start: A_TickCount
    }
    SetTimer(_PollPosts, 100)
}

_PollPosts(*) {
    global g_pending

    if (g_pending.Count == 0) {
        SetTimer(_PollPosts, 0)
        return
    }

    now := A_TickCount
    finished := []
    for rid, p in g_pending {
        if FileExist(p.respFile) {
            text := ""
            try text := FileRead(p.respFile, "UTF-8")
            try FileDelete(p.respFile)
            try FileDelete(p.reqFile)
            finished.Push(rid)
            _HandleResponse(p.action, text)
        } else if (now - p.start > 70000) {
            try FileDelete(p.reqFile)
            finished.Push(rid)
            _Notify("TOM", "Request timed out.`nMake sure TOM is running.", 3)
        }
    }
    for rid in finished
        g_pending.Delete(rid)
}

_HandleResponse(action, text) {
    resp := _ParseJSON(text)
    if (resp.Count == 0 || !resp.Has("ok")) {
        _Notify("TOM", "Could not reach TOM Brain.`nMake sure TOM is running.", 3)
        return
    }

    if (!resp["ok"]) {
        errMsg := resp.Has("error") ? resp["error"] : "Unknown error"
        _Notify("TOM Error", errMsg, 4)
        return
    }

    result := resp.Has("result") ? resp["result"] : ""

    ; â”€â”€ Special post-processing per action â”€â”€
    if (action == "status") {
        _Notify("TOM Status", result, 3)
        return
    }

    if (action == "ocr" && result != "" && result != "(no text found)") {
        A_Clipboard := result
        _Notify("TOM OCR", "Text copied to clipboard:`n" SubStr(result, 1, 120), 3)
        return
    }

    if (action == "note") {
        _Notify("TOM", result, 2)
        return
    }

    ; For all other actions show a brief tray tip; TOM speaks the full result
    ; via voice.announce when in voice mode.
    preview := SubStr(result, 1, 200)
    if (StrLen(result) > 200)
        preview .= "â€¦"
    if (preview != "")
        _Notify("TOM â€“ " StrTitle(action), preview, 5)
}

_BuildBody(action, context, region, rid, approved := false) {
    body := '{"action":"' action '","context":"' _JsonEscape(context) '","request_id":"' rid '","approved":' (approved ? "true" : "false")
    if (region != "")
        body .= ',"region":' region
    body .= "}"
    return body
}

; â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”
;  Helpers
; â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”

; ArcTan2 wrapper (degrees) â€“ returns -180..180
ArcTan(y, x) {
    if (x != 0)
        return ATan(y / x) + (x < 0 ? (y >= 0 ? 3.14159265 : -3.14159265) : 0)
    return y > 0 ? 1.5707963 : (y < 0 ? -1.5707963 : 0)
}

StrTitle(s) {
    return StrUpper(SubStr(s, 1, 1)) . SubStr(s, 2)
}

; JSON string escaping for the request body.
_JsonEscape(s) {
    out := ""
    Loop StrLen(s) {
        ch := SubStr(s, A_Index, 1)
        code := Ord(ch)
        if (ch == '\')
            out .= '\\'
        else if (ch == '"')
            out .= '\"'
        else if (ch == "`n")
            out .= '\n'
        else if (ch == "`r")
            out .= '\r'
        else if (ch == "`t")
            out .= '\t'
        else if (code < 32)
            out .= Format('\u{:04x}', code)
        else
            out .= ch
    }
    return out
}

; Parse a flat JSON object (the response schema is flat). Handles escaped
; quotes, backslashes, control characters and \uXXXX sequences correctly.
_ParseJSON(str) {
    result := Map()
    str := Trim(str, " `t`r`n")
    if (SubStr(str, 1, 1) != "{")
        return result

    n := StrLen(str)
    pos := 2
    while (pos <= n) {
        while (pos <= n && InStr(" `t`r`n,", SubStr(str, pos, 1)))
            pos++
        if (pos > n || SubStr(str, pos, 1) == "}")
            break
        if (SubStr(str, pos, 1) != '"')
            break
        key := _JsonReadString(str, &pos)
        while (pos <= n && InStr(" `t`r`n", SubStr(str, pos, 1)))
            pos++
        if (SubStr(str, pos, 1) != ":")
            break
        pos++
        result[key] := _JsonReadValue(str, &pos)
    }
    return result
}

_JsonReadString(str, &pos) {
    pos++   ; skip opening quote
    out := ""
    n := StrLen(str)
    while (pos <= n) {
        ch := SubStr(str, pos, 1)
        if (ch == '"') {
            pos++
            return out
        }
        if (ch == '\') {
            pos++
            if (pos > n)
                break
            esc := SubStr(str, pos, 1)
            if (esc == '"')
                out .= '"'
            else if (esc == '\')
                out .= '\'
            else if (esc == '/')
                out .= '/'
            else if (esc == 'b')
                out .= Chr(8)
            else if (esc == 'f')
                out .= Chr(12)
            else if (esc == 'n')
                out .= "`n"
            else if (esc == 'r')
                out .= "`r"
            else if (esc == 't')
                out .= "`t"
            else if (esc == 'u') {
                hex := SubStr(str, pos + 1, 4)
                if (StrLen(hex) < 4)
                    break
                out .= Chr(Integer("0x" hex))
                pos += 4
            } else
                out .= esc
        } else {
            out .= ch
        }
        pos++
    }
    return out
}

_JsonReadValue(str, &pos) {
    n := StrLen(str)
    while (pos <= n && InStr(" `t`r`n", SubStr(str, pos, 1)))
        pos++
    if (pos > n)
        return ""
    if (SubStr(str, pos, 1) == '"')
        return _JsonReadString(str, &pos)
    if (SubStr(str, pos, 4) == "true") {
        pos += 4
        return true
    }
    if (SubStr(str, pos, 5) == "false") {
        pos += 5
        return false
    }
    if (SubStr(str, pos, 4) == "null") {
        pos += 4
        return ""
    }

    start := pos
    while (pos <= n && !InStr(" `t`r`n,}", SubStr(str, pos, 1)))
        pos++
    return SubStr(str, start, pos - start)
}

; Per-monitor DPI awareness v2 so mouse, GDI+ and mss screen coordinates agree.
EnableDpiAwareness() {
    try {
        if DllCall("SetProcessDpiAwarenessContext", "Ptr", -4, "Int")
            return
    }
    try {
        DllCall("SetProcessDpiAwareness", "Int", 2)
        return
    }
    try DllCall("SetProcessDPIAware")
}

_MonitorScale(x, y) {
    ; Query the monitor at the activation point so mixed-DPI displays get
    ; appropriately sized hit targets and glyph strokes.
    try {
        point := ((y & 0xFFFFFFFF) << 32) | (x & 0xFFFFFFFF)
        monitor := DllCall("MonitorFromPoint", "Int64", point, "UInt", 2, "Ptr")
        if (monitor) {
            dpiX := 96, dpiY := 96
            hr := DllCall("Shcore\GetDpiForMonitor", "Ptr", monitor, "Int", 0,
                "UInt*", &dpiX, "UInt*", &dpiY, "Int")
            if (hr == 0 && dpiX > 0)
                return Min(3.0, Max(0.75, dpiX / 96.0))
        }
    }
    try return Min(3.0, Max(0.75, A_ScreenDPI / 96.0))
    return 1.0
}

; Show a Windows tray notification
_Notify(title, msg, seconds := 3) {
    TrayTip(title, msg)
    SetTimer(() => TrayTip(), -seconds * 1000)
}

; GDI+ initialisation
GdipCreateBrush(color) {
    brush := 0
    status := DllCall("gdiplus\GdipCreateSolidFill", "UInt", color, "Ptr*", &brush, "UInt")
    GdipRequire("GdipCreateSolidFill", status, brush, true)
    return brush
}

GdipCreatePen(color, width) {
    pen := 0
    status := DllCall("gdiplus\GdipCreatePen1", "UInt", color, "Float", width,
        "Int", 2, "Ptr*", &pen, "UInt")
    GdipRequire("GdipCreatePen1", status, pen, true)
    return pen
}

GdiplusInit() {
    global pToken, hGdiPlus
    if (pToken)
        return pToken

    if (!hGdiPlus) {
        hGdiPlus := DllCall("LoadLibrary", "Str", "gdiplus", "Ptr")
        if (!hGdiPlus)
            throw Error("LoadLibrary(gdiplus) failed; Win32 error=" . A_LastError)
    }

    ; GdiplusStartupInput contains a pointer and two BOOLs after the version.
    ; Its native size is 24 bytes on x64 and 16 bytes on x86.
    si := Buffer(A_PtrSize == 8 ? 24 : 16, 0)
    NumPut("UInt", 1, si, 0) ; GdiplusVersion
    pTok := 0
    status := DllCall("gdiplus\GdiplusStartup", "Ptr*", &pTok, "Ptr", si, "Ptr", 0, "UInt")
    GdipRequire("GdiplusStartup", status, pTok, true)
    pToken := pTok
    return pToken
}

GdipRequire(functionName, status, resourcePtr := 0, requirePtr := false) {
    if (status != 0 || (requirePtr && !resourcePtr)) {
        ptrText := Format("0x{:X}", resourcePtr)
        throw Error("GDI+ " . functionName . " failed: status=" . status
            . ", pointer=" . ptrText)
    }
}

GdipDeleteChecked(functionName, &resourcePtr) {
    if (!resourcePtr)
        return
    ptr := resourcePtr
    resourcePtr := 0
    status := DllCall("gdiplus\" . functionName, "Ptr", ptr, "UInt")
    GdipRequire(functionName, status, ptr)
}

; â”€â”€ Graceful exit: shutdown GDI+ â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
_Cleanup(reason, code) {
    global pToken, hGdiPlus
    token := pToken
    pToken := 0
    if (token)
        DllCall("gdiplus\GdiplusShutdown", "Ptr", token)

    module := hGdiPlus
    hGdiPlus := 0
    if (module)
        DllCall("FreeLibrary", "Ptr", module, "Int")
}
