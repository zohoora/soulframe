#version 330 core

in vec2 v_texcoord;
out vec4 fragColor;

uniform sampler2D u_texture;
uniform vec2 u_gaze_pos;       // normalized 0-1, where the viewer is looking
uniform float u_intensity;     // 0-1
uniform float u_depth_scale;   // default 0.01

void main() {
    vec2 uv = v_texcoord;

    // Offset from gaze position (center of attention)
    // Pixels further from gaze shift more, creating a 2.5D parallax feel
    vec2 gaze_offset = uv - u_gaze_pos;

    // The shift is proportional to the distance from gaze and the depth scale
    // This makes the image appear to subtly warp around the gaze point
    vec2 shift = gaze_offset * u_depth_scale * u_intensity;

    uv -= shift;

    fragColor = texture(u_texture, uv);
}
