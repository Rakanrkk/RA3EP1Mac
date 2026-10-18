#include "viewport_math.h"
#include <assert.h>
#include <stdio.h>

static void close_to(double actual, double expected) {
    assert(fabs(actual - expected) < 1e-8);
}

int main(void) {
    Ra3ViewportRect output;
    assert(ra3_viewport_bottom_aligned((Ra3ViewportRect){0, 0, 3024, 1964},
                                       64, 3024, 1900, &output));
    close_to(output.x, 0);
    close_to(output.y, 64);
    close_to(output.width, 3024);
    close_to(output.height, 1900);
    assert(!ra3_viewport_bottom_aligned((Ra3ViewportRect){0, 0, 3024, 1964},
                                        64, 3024, 1920, &output));
    assert(!ra3_viewport_bottom_aligned((Ra3ViewportRect){0, 0, 1920, 1080},
                                        0, 3024, 1900, &output));
    assert(!ra3_viewport_bottom_aligned((Ra3ViewportRect){0, 0, 3024, 1964},
                                        NAN, 3024, 1900, &output));

    const double sizes[][2] = {{800, 600}, {1024, 768}, {1280, 720},
                              {2560, 1440}, {2560, 1600}, {3024, 1900}};
    unsigned checks = 0;
    for (unsigned index = 0; index < sizeof(sizes)/sizeof(sizes[0]); index++) {
        Ra3ViewportTransform transform = {
            {0, 0, sizes[index][0], sizes[index][1]}, {0, 64, 3024, 1900}};
        for (unsigned y = 0; y <= 10; y++) {
            for (unsigned x = 0; x <= 10; x++) {
                double source_x = transform.source.width * x / 10;
                double source_y = transform.source.height * y / 10;
                double native_x, native_y, back_x, back_y;
                assert(ra3_viewport_map(transform, source_x, source_y, &native_x, &native_y));
                assert(ra3_viewport_inverse(transform, native_x, native_y, &back_x, &back_y));
                close_to(back_x, source_x);
                close_to(back_y, source_y);
                checks++;
            }
        }
        double x, y;
        assert(ra3_viewport_inverse_pixel(transform, 3024, 1964, &x, &y));
        close_to(x, transform.source.width - 1);
        close_to(y, transform.source.height - 1);
        assert(ra3_viewport_inverse_pixel(transform, -30, 0, &x, &y));
        close_to(x, 0);
        close_to(y, 0);
    }
    Ra3ViewportTransform shifted = {{50, 100, 2560, 1600}, {-1512, 37, 1512, 945}};
    double x, y;
    assert(ra3_viewport_map(shifted, 50, 100, &x, &y));
    close_to(x, -1512);
    close_to(y, 37);
    assert(!ra3_viewport_map(shifted, NAN, 0, &x, &y));
    assert(!ra3_viewport_inverse((Ra3ViewportTransform){{0, 0, 0, 1}, {0, 0, 1, 1}}, 0, 0, &x, &y));
    printf("PASS: %u viewport inverse checks, fixed extent, edges, origins and invalid inputs\n", checks);
    return 0;
}
