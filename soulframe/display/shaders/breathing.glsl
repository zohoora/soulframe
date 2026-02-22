#version 330 core

in vec2 v_texcoord;
out vec4 fragColor;

uniform sampler2D u_texture;
uniform float u_time;
uniform float u_amplitude;      // default ~0.008
uniform float u_frequency;      // default ~0.25 Hz
uniform float u_intensity;      // 0-1, for fade in/out
uniform vec2 u_region_center;   // center of breathing region (normalized 0-1)
uniform float u_region_radius;  // radius of breathing region (normalized)

void main() {
    vec2 uv = v_texcoord;

    // Distance from the region center
    vec2 delta = uv - u_region_center;
    float dist = length(delta);

    // Smooth falloff: 1.0 inside the region, fading to 0.0 at and beyond the boundary
    float falloff = 1.0 - smoothstep(u_region_radius * 0.6, u_region_radius, dist);

    // Breathing scale factor: sine wave oscillation
    float breath = sin(u_time * u_frequency * 6.2831853) * u_amplitude;

    // Combined effect strength
    float strength = breath * falloff * u_intensity;

    // Scale UVs around the region center
    uv = u_region_center + delta * (1.0 + strength);

    fragColor = texture(u_texture, uv);
}
