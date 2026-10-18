#define WIN32_LEAN_AND_MEAN
#include <windows.h>
#include <stdio.h>
#include <stdlib.h>

/* Opt-in probe process only: restore its original pointer/clip before exit. */
int main(void) {
    POINT saved = {0}, actual = {0};
    RECT saved_clip = {0};
    if (!SetProcessDPIAware() && !IsProcessDPIAware()) return 10;
    if (!GetCursorPos(&saved) || !GetClipCursor(&saved_clip)) return 11;
    const int width = GetSystemMetrics(SM_CXSCREEN);
    const int height = GetSystemMetrics(SM_CYSCREEN);
    if (width < 320 || height < 200) return 12;
    const POINT points[] = {{0, 0}, {width-1, 0}, {width-1, height-1},
                            {0, height-1}, {width/2, height/2}};
    int status = 42;
    printf("screen=%dx%d original=%ld,%ld clip=%ld,%ld,%ld,%ld\n", width, height,
        saved.x, saved.y, saved_clip.left, saved_clip.top, saved_clip.right, saved_clip.bottom);
    fflush(stdout);
    if (!ClipCursor(NULL)) status = 13;
    for (unsigned i = 0; status == 42 && i < sizeof(points)/sizeof(points[0]); i++) {
        if (!SetCursorPos(points[i].x, points[i].y)) { status = 14; break; }
        if (!GetCursorPos(&actual)) { status = 15; break; }
        printf("point=%u expected=%ld,%ld actual=%ld,%ld\n", i,
            points[i].x, points[i].y, actual.x, actual.y);
        fflush(stdout);
        if (labs(actual.x-points[i].x) > 1 || labs(actual.y-points[i].y) > 1) status = 16;
        Sleep(400);
    }
    if (!ClipCursor(&saved_clip)) status = 17;
    if (!SetCursorPos(saved.x, saved.y)) status = 18;
    if (status == 42) puts("PASS: absolute pointer round trips and clip restore");
    return status;
}
