# Example custom tool. Files in this folder are loaded on startup.
# The assistant writes files in exactly this format when it creates tools for itself.
TOOL_SPEC = {
    "name": "example_server_stats",
    "description": "Count humans vs bots and list the roles with the most members.",
    "parameters": {
        "type": "object",
        "properties": {"top": {"type": "integer", "description": "How many roles to list (default 10)."}},
    },
    "destructive": False,
}


async def run(ctx, args):
    members = ctx.guild.members
    roles = sorted(
        (r for r in ctx.guild.roles if not r.is_default()), key=lambda r: len(r.members), reverse=True
    )
    top = int(args.get("top") or 10)
    return {
        "total": len(members),
        "humans": sum(not m.bot for m in members),
        "bots": sum(m.bot for m in members),
        "top_roles": {r.name: len(r.members) for r in roles[:top]},
    }
