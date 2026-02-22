#version 330 core

in vec2 v_texcoord;
out vec4 fragColor;

uniform sampler2D u_texture;
uniform float u_time;
uniform float u_zoom_speed;      // e.g. 0.001 per second
uniform vec2 u_pan_direction;    // normalized direction for slow pan
uniform float u_intensity;       // 0-1

void main() {
    vec2 uv = v_texcoord;

    // Very slow zoom: scale UVs toward center over time
    float zoom = 1.0 + u_time * u_zoom_speed * u_intensity;

    // Zoom around the center of the image
    vec2 center = vec2(0.5, 0.5);
    uv = center + (uv - center) / zoom;

    // Subtle pan in the given direction over time
    vec2 pan_offset = u_pan_direction * u_time * u_zoom_speed * 0.5 * u_intensity;
    uv += pan_offset;

    fragColor = texture(u_texture, uv);
}
