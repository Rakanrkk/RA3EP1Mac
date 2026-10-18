/* Read-only, bounded Cocoa view/confinement inspection for owned Wine probes. */
#import <AppKit/AppKit.h>
#import <QuartzCore/QuartzCore.h>
#import <objc/message.h>
#import <fcntl.h>
#import <unistd.h>

static int output_fd = -1;
static unsigned ticks;

static NSDictionary *rect_dict(NSRect rect) {
    return @{ @"x": @(rect.origin.x), @"y": @(rect.origin.y),
              @"w": @(rect.size.width), @"h": @(rect.size.height) };
}

static NSDictionary *layer_dict(CALayer *layer, unsigned depth) {
    if (!layer) return @{};
    NSMutableDictionary *result = [@{
        @"class": NSStringFromClass(layer.class), @"frame": rect_dict(layer.frame),
        @"bounds": rect_dict(layer.bounds), @"position": @[@(layer.position.x), @(layer.position.y)],
        @"anchor": @[@(layer.anchorPoint.x), @(layer.anchorPoint.y)],
        @"flipped": @(layer.geometryFlipped), @"mask": @(layer.masksToBounds),
        @"scale": @(layer.contentsScale), @"hidden": @(layer.hidden)
    } mutableCopy];
    if (depth) {
        NSMutableArray *children = [NSMutableArray array];
        for (CALayer *child in layer.sublayers) {
            if (children.count == 8) break;
            [children addObject:layer_dict(child, depth-1)];
        }
        result[@"children"] = children;
    }
    return result;
}

static NSDictionary *view_dict(NSView *view, unsigned depth) {
    if (!view) return @{};
    NSWindow *window = view.window;
    NSRect screen = [window convertRectToScreen:[view convertRect:view.bounds toView:nil]];
    NSMutableDictionary *result = [@{
        @"class": NSStringFromClass(view.class), @"frame": rect_dict(view.frame),
        @"bounds": rect_dict(view.bounds), @"screen": rect_dict(screen),
        @"visibleRect": rect_dict(view.visibleRect), @"flipped": @(view.isFlipped),
        @"hidden": @(view.hidden), @"layer": layer_dict(view.layer, 1)
    } mutableCopy];
    if (depth) {
        NSMutableArray *children = [NSMutableArray array];
        for (NSView *child in view.subviews) {
            if (children.count == 8) break;
            [children addObject:view_dict(child, depth-1)];
        }
        result[@"children"] = children;
    }
    return result;
}

static void inspect_tick(void) {
    if (output_fd < 0) return;
    @autoreleasepool {
        NSMutableArray *windows = [NSMutableArray array];
        for (NSWindow *window in NSApp.windows) {
            if (windows.count == 8) break;
            if (![NSStringFromClass(window.class) containsString:@"WineWindow"]) continue;
            @try {
                NSMutableDictionary *item = [@{
                    @"frame": rect_dict(window.frame), @"key": @(window.keyWindow),
                    @"visible": @(window.visible), @"number": @(window.windowNumber),
                    @"content": view_dict(window.contentView, 4)
                } mutableCopy];
                SEL selector = NSSelectorFromString(@"mouseConfinementRect");
                if ([window respondsToSelector:selector]) {
                    NSRect (*get_rect)(id, SEL) = (NSRect (*)(id, SEL))objc_msgSend_stret;
                    item[@"confinement"] = rect_dict(get_rect(window, selector));
                }
                [windows addObject:item];
            } @catch (NSException *exception) {
                [windows addObject:@{@"exception": exception.name}];
            }
        }
        NSDictionary *record = @{ @"pid": @(getpid()), @"tick": @(ticks),
                                   @"windows": windows, @"read_only": @YES };
        NSData *data = [NSJSONSerialization dataWithJSONObject:record options:0 error:nil];
        if (data) { write(output_fd, data.bytes, data.length); write(output_fd, "\n", 1); }
    }
    if (++ticks >= 13) { close(output_fd); output_fd = -1; return; }
    dispatch_after(dispatch_time(DISPATCH_TIME_NOW, NSEC_PER_SEC), dispatch_get_main_queue(), ^{ inspect_tick(); });
}

__attribute__((constructor)) static void inspect_start(void) {
    const char *path = getenv("RA3_INSPECT_CONFINEMENT");
    if (!path || path[0] != '/') return;
    char output[4096];
    int length = snprintf(output, sizeof(output), "%s.%d.jsonl", path, getpid());
    if (length < 0 || (size_t)length >= sizeof(output)) return;
    output_fd = open(output, O_WRONLY | O_CREAT | O_EXCL, 0600);
    if (output_fd < 0) return;
    dispatch_async(dispatch_get_main_queue(), ^{ inspect_tick(); });
}

__attribute__((destructor)) static void inspect_end(void) {
    if (output_fd >= 0) close(output_fd);
    output_fd = -1;
}
