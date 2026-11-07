package com.cloudphone.client;

import static org.junit.Assert.assertEquals;
import static org.junit.Assert.assertNotNull;
import static org.junit.Assert.assertNull;

import org.junit.Test;

public final class VideoCoordinateMapperTest {
    @Test public void removesHorizontalLetterboxBeforeNormalizing() {
        VideoCoordinateMapper mapper = new VideoCoordinateMapper();
        mapper.updateViewSize(200, 100);
        mapper.updateVideoSize(100, 100);
        assertNull(mapper.map(49, 50, false));
        assertEquals(0, require(mapper.map(50, 0, false)).x);
        assertNear(32_768, require(mapper.map(100, 50, false)).x);
        assertEquals(65_535, require(mapper.map(150, 100, false)).x);
    }

    @Test public void clampsActivePointerWhenItLeavesVideo() {
        VideoCoordinateMapper mapper = new VideoCoordinateMapper();
        mapper.updateViewSize(100, 200);
        mapper.updateVideoSize(100, 100);
        VideoCoordinateMapper.MappedPoint point = require(mapper.map(20, 220, true));
        assertNear(13_107, point.x);
        assertEquals(65_535, point.y);
    }

    @Test public void returnsNullUntilBothGeometriesAreKnown() {
        assertNull(new VideoCoordinateMapper().map(0, 0, true));
    }

    private static VideoCoordinateMapper.MappedPoint require(VideoCoordinateMapper.MappedPoint point) {
        assertNotNull(point);
        return point;
    }

    private static void assertNear(int expected, int actual) {
        assertEquals(expected, actual, 1);
    }
}

