"""
Interactive embed paginator using discord.py Views.

Usage:
    pages = [discord.Embed(...), discord.Embed(...), ...]
    view  = Paginator(pages, ctx.author)
    await ctx.send(embed=pages[0], view=view)

Or use the helper:
    await send_paginated(ctx, pages)
"""

import discord
from discord.ext import commands


class Paginator(discord.ui.View):
    """
    A button-driven embed paginator.

    Features:
    - First / Prev / Page indicator / Next / Last buttons
    - Only the invoking user can interact (others get an ephemeral error)
    - Auto-disables all buttons after `timeout` seconds of inactivity
    - Cleans up gracefully on timeout (buttons disabled, message edited)
    """

    def __init__(
        self,
        pages: list[discord.Embed],
        author: discord.User | discord.Member,
        *,
        timeout: float = 120.0,
    ):
        super().__init__(timeout=timeout)
        if not pages:
            raise ValueError("Paginator requires at least one page.")

        self.pages   = pages
        self.author  = author
        self.current = 0
        self.message: discord.Message | None = None

        self._update_buttons()

    # ── Button state ───────────────────────────────────────────
    def _update_buttons(self) -> None:
        total = len(self.pages)
        at_start = self.current == 0
        at_end   = self.current == total - 1

        self.btn_first.disabled = at_start
        self.btn_prev.disabled  = at_start
        self.btn_next.disabled  = at_end
        self.btn_last.disabled  = at_end
        self.btn_page.label     = f"{self.current + 1} / {total}"

    async def _update_message(self, interaction: discord.Interaction) -> None:
        self._update_buttons()
        await interaction.response.edit_message(
            embed=self.pages[self.current],
            view=self,
        )

    # ── Auth guard ─────────────────────────────────────────────
    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.author.id:
            await interaction.response.send_message(
                "❌ Only the person who ran this command can use these buttons.",
                ephemeral=True,
            )
            return False
        return True

    # ── Timeout ────────────────────────────────────────────────
    async def on_timeout(self) -> None:
        for child in self.children:
            if isinstance(child, discord.ui.Button):
                child.disabled = True
        if self.message:
            try:
                await self.message.edit(view=self)
            except discord.HTTPException:
                pass

    # ── Buttons ────────────────────────────────────────────────
    @discord.ui.button(label="«", style=discord.ButtonStyle.grey)
    async def btn_first(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        self.current = 0
        await self._update_message(interaction)

    @discord.ui.button(label="‹", style=discord.ButtonStyle.blurple)
    async def btn_prev(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        self.current = max(0, self.current - 1)
        await self._update_message(interaction)

    @discord.ui.button(label="1 / 1", style=discord.ButtonStyle.grey, disabled=True)
    async def btn_page(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        # Page indicator — not interactive, always disabled
        await interaction.response.defer()

    @discord.ui.button(label="›", style=discord.ButtonStyle.blurple)
    async def btn_next(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        self.current = min(len(self.pages) - 1, self.current + 1)
        await self._update_message(interaction)

    @discord.ui.button(label="»", style=discord.ButtonStyle.grey)
    async def btn_last(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        self.current = len(self.pages) - 1
        await self._update_message(interaction)


# ── Confirmation View ──────────────────────────────────────────

class ConfirmView(discord.ui.View):
    """
    A simple Yes / No confirmation view.

    Usage:
        view = ConfirmView(ctx.author)
        msg  = await ctx.send("Are you sure?", view=view)
        await view.wait()
        if view.confirmed is True:
            ...
        elif view.confirmed is False:
            ...
        else:
            # Timed out
            ...
    """

    def __init__(
        self,
        author: discord.User | discord.Member,
        *,
        timeout: float = 30.0,
        confirm_label: str = "Confirm",
        cancel_label: str  = "Cancel",
    ):
        super().__init__(timeout=timeout)
        self.author    = author
        self.confirmed: bool | None = None  # None = timed out

        self.btn_confirm.label = confirm_label
        self.btn_cancel.label  = cancel_label

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.author.id:
            await interaction.response.send_message(
                "❌ Only the person who ran this command can use these buttons.",
                ephemeral=True,
            )
            return False
        return True

    async def _finish(
        self,
        interaction: discord.Interaction,
        confirmed: bool,
    ) -> None:
        self.confirmed = confirmed
        for child in self.children:
            if isinstance(child, discord.ui.Button):
                child.disabled = True
        await interaction.response.edit_message(view=self)
        self.stop()

    @discord.ui.button(label="Confirm", style=discord.ButtonStyle.danger)
    async def btn_confirm(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        await self._finish(interaction, confirmed=True)

    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.grey)
    async def btn_cancel(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        await self._finish(interaction, confirmed=False)

    async def on_timeout(self) -> None:
        self.confirmed = None
        self.stop()


# ── Helper functions ───────────────────────────────────────────

async def send_paginated(
    ctx: commands.Context,
    pages: list[discord.Embed],
    *,
    timeout: float = 120.0,
) -> discord.Message:
    """
    Send a paginated embed list to the context channel.
    If only one page, sends without buttons.

    Returns the sent Message object.
    """
    if not pages:
        raise ValueError("No pages to send.")

    if len(pages) == 1:
        return await ctx.send(embed=pages[0])

    view = Paginator(pages, ctx.author, timeout=timeout)
    msg  = await ctx.send(embed=pages[0], view=view)
    view.message = msg
    return msg


async def send_confirm(
    ctx: commands.Context,
    prompt: str,
    *,
    timeout: float = 30.0,
    confirm_label: str = "Confirm",
    cancel_label:  str = "Cancel",
) -> bool | None:
    """
    Send a confirmation prompt with Yes/No buttons.

    Returns:
        True   — user confirmed
        False  — user cancelled
        None   — timed out
    """
    view = ConfirmView(
        ctx.author,
        timeout=timeout,
        confirm_label=confirm_label,
        cancel_label=cancel_label,
    )
    await ctx.send(prompt, view=view)
    await view.wait()
    return view.confirmed


def build_pages(
    items:       list[str],
    title:       str        = "",
    colour:      discord.Colour = discord.Colour.blurple(),
    *,
    per_page:    int        = 10,
    prefix:      str        = "",
    suffix:      str        = "",
    numbered:    bool       = True,
    footer:      str        = "",
) -> list[discord.Embed]:
    """
    Build a list of embeds from a list of string items.

    Args:
        items:    Lines to paginate.
        title:    Embed title (page number appended automatically).
        colour:   Embed colour.
        per_page: Items per embed.
        prefix:   Text to prepend to each page's description.
        suffix:   Text to append to each page's description.
        numbered: If True, prepend line numbers to each item.
        footer:   Footer text (page X/Y appended automatically).

    Returns:
        List of discord.Embed objects.
    """
    if not items:
        embed = discord.Embed(
            title=title,
            description="*(nothing to show)*",
            colour=colour,
        )
        return [embed]

    chunks = [items[i : i + per_page] for i in range(0, len(items), per_page)]
    total  = len(chunks)
    pages  = []

    for idx, chunk in enumerate(chunks, start=1):
        if numbered:
            start = (idx - 1) * per_page + 1
            lines = [f"`{start + i}.` {line}" for i, line in enumerate(chunk)]
        else:
            lines = list(chunk)

        description = "\n".join(filter(None, [prefix, "\n".join(lines), suffix]))
        embed = discord.Embed(
            title=f"{title} — Page {idx}/{total}" if title else f"Page {idx}/{total}",
            description=description,
            colour=colour,
        )
        if footer:
            embed.set_footer(text=f"{footer} • Page {idx}/{total}")
        else:
            embed.set_footer(text=f"Page {idx}/{total} • {len(items)} total")

        pages.append(embed)

    return pages