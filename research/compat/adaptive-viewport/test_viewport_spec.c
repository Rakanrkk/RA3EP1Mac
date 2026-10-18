#include "viewport_spec.h"

#include <assert.h>
#include <stdio.h>
#include <string.h>

static int g_checks = 0;

static Ra3ViewportSpec make_sentinel(void) {
    const Ra3ViewportSpec spec = {111, 222, 333, 444, 555, 666};
    return spec;
}

static int specs_equal(Ra3ViewportSpec a, Ra3ViewportSpec b) {
    return a.screen_width == b.screen_width && a.screen_height == b.screen_height &&
           a.left == b.left && a.top == b.top && a.right == b.right &&
           a.bottom == b.bottom;
}

/* Success: returns 1 with exact fields, input buffer byte-identical. */
static void expect_parse_ok(const char *value, Ra3ViewportSpec expected) {
    char buffer[256];
    Ra3ViewportSpec spec = make_sentinel();
    const size_t length = strlen(value);
    assert(length < sizeof buffer);
    memcpy(buffer, value, length + 1);
    if (ra3_parse_viewport_spec(buffer, &spec) != 1) {
        fprintf(stderr, "expected success for '%s'\n", value);
        assert(0);
    }
    assert(memcmp(buffer, value, length + 1) == 0);
    assert(specs_equal(spec, expected));
    g_checks++;
}

/* Failure: returns 0 and the sentinel output is untouched. */
static void expect_parse_fail(const char *value) {
    Ra3ViewportSpec spec = make_sentinel();
    const Ra3ViewportSpec before = spec;
    if (ra3_parse_viewport_spec(value, &spec) != 0) {
        fprintf(stderr, "expected failure for '%s'\n", value);
        assert(0);
    }
    assert(specs_equal(spec, before));
    g_checks++;
}

int main(void) {
    /* Valid encodings across display shapes (Wine raw coordinate units). */
    expect_parse_ok("3024x1964:0,64,3024,1964",
                    (Ra3ViewportSpec){3024, 1964, 0, 64, 3024, 1964}); /* notched laptop */
    expect_parse_ok("2880x1800:0,0,2880,1800",
                    (Ra3ViewportSpec){2880, 1800, 0, 0, 2880, 1800}); /* no-notch, zero origin */
    expect_parse_ok("7680x4320:0,0,7680,4320",
                    (Ra3ViewportSpec){7680, 4320, 0, 0, 7680, 4320}); /* external 8K */
    expect_parse_ok("3840x2160:111,75,3777,2107",
                    (Ra3ViewportSpec){3840, 2160, 111, 75, 3777, 2107}); /* odd nonzero insets */
    expect_parse_ok("3024x1964:1512,982,3024,1964",
                    (Ra3ViewportSpec){3024, 1964, 1512, 982, 3024, 1964}); /* quadrant offsets */
    expect_parse_ok("1000x700:0,0,320,200",
                    (Ra3ViewportSpec){1000, 700, 0, 0, 320, 200}); /* minimum viewport */
    expect_parse_ok("32768x32768:0,0,32768,32768",
                    (Ra3ViewportSpec){32768, 32768, 0, 0, 32768, 32768}); /* cap values */
    expect_parse_ok("01000x0700:0000,0000,00320,00200",
                    (Ra3ViewportSpec){1000, 700, 0, 0, 320, 200}); /* leading zeros tolerated */

    /* Malformed: whitespace, signs, hex-style, separators, missing fields. */
    expect_parse_fail("");
    expect_parse_fail("   ");
    expect_parse_fail(" 3024x1964:0,64,3024,1964");
    expect_parse_fail("3024 x1964:0,64,3024,1964");
    expect_parse_fail("3024x 1964:0,64,3024,1964");
    expect_parse_fail("3024x1964: 0,64,3024,1964");
    expect_parse_fail("3024x1964:0,64,3024,1964 ");
    expect_parse_fail("3024x1964:0,64,3024,1964\n");
    expect_parse_fail("+3024x1964:0,64,3024,1964");
    expect_parse_fail("-3024x1964:0,64,3024,1964");
    expect_parse_fail("3024x1964:0,+64,3024,1964");
    expect_parse_fail("3024X1964:0,64,3024,1964"); /* uppercase X */
    expect_parse_fail("0X3024:0,64,3024,1964");
    expect_parse_fail("0x3024x1964:0,64,3024,1964"); /* hex-looking */
    expect_parse_fail("3024xy1964:0,64,3024,1964");
    expect_parse_fail("3024x1964:0x64,3024,1964");
    expect_parse_fail("abc");
    expect_parse_fail("3024");          /* missing screen height */
    expect_parse_fail("3024x1964");     /* missing rect entirely */
    expect_parse_fail("3024x1964:");
    expect_parse_fail(":0,64,3024,1964");
    expect_parse_fail("3024x1964::0,64,3024,1964");
    expect_parse_fail("x1964:0,64,3024,1964");
    expect_parse_fail("3024x:0,64,3024,1964");
    expect_parse_fail("3024x1964:0,64,3024");        /* 3 rect fields */
    expect_parse_fail("3024x1964:0,64,3024,1964,0"); /* 5 rect fields */
    expect_parse_fail("3024x1964:,64,3024,1964");
    expect_parse_fail("3024x1964:0,,3024,1964");
    expect_parse_fail("3024x1964:0,64,,1964");
    expect_parse_fail("3024x1964:0,64,3024,");
    expect_parse_fail("3024x1964:0,64,3024,1964x");

    /* NULL arguments. */
    expect_parse_fail(0);
    {
        Ra3ViewportSpec spec = make_sentinel();
        assert(ra3_parse_viewport_spec("3024x1964:0,64,3024,1964", 0) == 0);
        assert(specs_equal(spec, make_sentinel()));
        g_checks++;
    }

    /* Parsed but out of contract: zero extents, cap and fit breaks, overflow. */
    expect_parse_fail("0x1964:0,64,3024,1964"); /* screen width 0 */
    expect_parse_fail("0x0:0,0,320,200");
    expect_parse_fail("32769x1964:0,64,3024,1964"); /* over cap */
    expect_parse_fail("40000x1964:0,64,3024,1964");
    expect_parse_fail("3024x1964:0,64,3024,1965");    /* bottom past screen */
    expect_parse_fail("3024x1964:0,64,3025,1964");    /* right past screen */
    expect_parse_fail("3024x1964:0,64,40000,1964");
    expect_parse_fail("3024x1964:3024,64,3024,1964"); /* empty width */
    expect_parse_fail("3024x1964:0,1000,320,1000");   /* empty height */
    expect_parse_fail("3024x1964:0,64,319,264");      /* width 319 */
    expect_parse_fail("3024x1964:0,64,320,263");      /* height 199 */
    expect_parse_fail("3024x1964:64,0,10,5");         /* right < left */
    expect_parse_fail("99999999999999999999x1964:0,64,3024,1964");
    expect_parse_fail("3024x1964:0,64,99999999999999999999,1964");
    {
        static char huge[512];
        memset(huge, '9', sizeof huge - 1);
        huge[sizeof huge - 1] = '\0';
        expect_parse_fail(huge); /* must not overflow accumulation */
    }

    /* Failure must also leave the input bytes untouched. */
    {
        char buffer[] = "3024x1964:0,64,3024,1964 ";
        char before[sizeof buffer];
        Ra3ViewportSpec spec = make_sentinel();
        memcpy(before, buffer, sizeof buffer);
        assert(ra3_parse_viewport_spec(buffer, &spec) == 0);
        assert(memcmp(buffer, before, sizeof buffer) == 0);
        assert(specs_equal(spec, make_sentinel()));
        g_checks++;
    }

    printf("PASS: %d viewport spec checks (strict boolean parse: valid, NULL, "
           "malformed, overflow; failure atomicity)\n", g_checks);
    return 0;
}
