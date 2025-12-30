# Memory Bank - Blender Vertex Measurements Monetization

## Brainstorming Session: 12/30/2025

### Monetization Strategies Discussed

#### 1. Multi-Version Approach (Initial Idea)
- **Free/Limited Version**: Reduced features (e.g., limited max pairs, disabled settings, watermarks)
- **Premium Version**: Full features
- **Git Structure**: Use branches (`free`, `premium`) and tags (`v1.0-free`, `v1.0-premium`)

**Decision**: Postponed - User decided against free version

#### 2. Private Repository Access Model (Current Preferred)
- **Strategy**: Sell access to the full-featured add-on via private GitHub repository
- **Implementation**:
  - Make repository private
  - Collect payments via PayPal/Stripe/Gumroad
  - Upon payment, invite buyers as GitHub collaborators
  - Provide direct access to code and updates
- **Advantages**:
  - Simple implementation (no code changes needed)
  - Secure (GitHub handles access control)
  - Scalable for managing multiple users
  - Buyers get automatic updates
  - Code transparency builds trust
- **Pricing Model**: TBD (one-time purchase vs subscription)

### Technical Considerations

#### Repository Management
- Current: Public repository with README and screenshot
- Future: Private repository with collaborator-based access
- No immediate code modifications needed for monetization

#### Git Structure Options
- Keep `master` as development branch
- Consider release branches/tags for version management
- Tags for releases: `v1.0`, `v2.0`, etc.

### Next Steps
- Monitor interest in the add-on
- Research pricing strategies for Blender add-ons
- Evaluate payment platforms
- Consider Blender Market as alternative distribution

### Notes
- User wants to monetize the full add-on as-is
- No feature limitations planned
- Focus on simplicity and security
- Keep brainstorming open for future ideas

---

*This memory bank serves as a reference for future monetization implementation. Update as new ideas emerge.*
