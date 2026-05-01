"""
Reusable discord.py UI components (discord.ui.View subclasses).

Provides:
    ConfirmView      — Yes / No confirmation prompt
    DangerConfirmView — Destructive action confirmation (styled red)
    TimeoutView      — Base view that disables all buttons on timeout
    PaginatorView    — Next / Prev / Stop paginated embed navigator
"""

from __future__ import annotations

import discord


class TimeoutView(discord.ui.View):
    """
    Base view that disables all child components when it times out,
    editing the original response to reflect the disabled state.
    """

    def __init__(self, timeout: float = 60.0) -> None:
        super().__init__(timeout=timeout)
        self.message: discord.Message | None = None

    async def on_timeout(self) -> None:
        for child in self.children:
            child.disabled = True  # type: ignore[union-attr]
        if self.message:
            try:
                await self.message.edit(view=self)
            except discord.HTTPException:
                pass


# ── Confirm View ───────────────────────────────────────────────

class ConfirmView(TimeoutView):
    """
    Two-button Yes / No prompt.

    Usage:
        view = ConfirmView(ctx.author)
        msg  = await ctx.send("Are you sure?", view=view)
        view.message = msg
        await view.wait()
        if view.confirmed is True:
            ...
        elif view.confirmed is False:
            ...
        else:
            # timed out
            ...
    """

    def __init__(
        self,
        author:       discord.User | discord.Member,
        *,
        timeout:      float = 30.0,
        confirm_label: str  = "Confirm",
        cancel_label:  str  = "Cancel",
    ) -> None:
        super().__init__(timeout=timeout)
        self.author    = author
        self.confirmed: bool | None = None

    async def interaction_check(
        self,
        interaction: discord.Interaction,
    ) -> bool:
        if interaction.user.id != self.author.id:
            await interaction.response.send_message(
                "This confirmation isn't for you.", ephemeral=True
            )
            return False
        return True

    @discord.ui.button(label="Confirm", style=discord.ButtonStyle.green)
    async def confirm_btn(
        self,
        interaction: discord.Interaction,
        button:      discord.ui.Button,
    ) -> None:
        self.confirmed = True
        self._disable_all()
        await interaction.response.edit_message(view=self)
        self.stop()

    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.grey)
    async def cancel_btn(
        self,
        interaction: discord.Interaction,
        button:      discord.ui.Button,
    ) -> None:
        self.confirmed = False
        self._disable_all()
        await interaction.response.edit_message(view=self)
        self.stop()

    def _disable_all(self) -> None:
        for child in self.children:
            child.disabled = True  # type: ignore[union-attr]


class DangerConfirmView(ConfirmView):
    """
    Same as ConfirmView but the confirm button is styled red to
    signal a destructive / irreversible action.
    """

    @discord.ui.button(label="Confirm", style=discord.ButtonStyle.red)
    async def confirm_btn(
        self,
        interaction: discord.Interaction,
        button:      discord.ui.Button,
    ) -> None:
        self.confirmed = True
        self._disable_all()
        await interaction.response.edit_message(view=self)
        self.stop()


# ── Paginator View ─────────────────────────────────────────────

class PaginatorView(TimeoutView):
    """
    Interactive paginator for a list of :class:`discord.Embed` pages.

    Usage:
        pages = [embed1, embed2, embed3]
        view  = PaginatorView(pages, author=ctx.author)
        msg   = await ctx.send(embed=pages[0], view=view)
        view.message = msg
    """

    def __init__(
        self,
        pages:   list[discord.Embed],
        *,
        author:  discord.User | discord.Member | None = None,
        timeout: float = 120.0,
    ) -> None:
        super().__init__(timeout=timeout)
        if not pages:
            raise ValueError("PaginatorView requires at least one page.")
        self.pages   = pages
        self.author  = author
        self.current = 0
        self._update_buttons()

    async def interaction_check(
        self,
        interaction: discord.Interaction,
    ) -> bool:
        if self.author and interaction.user.id != self.author.id:
            await interaction.response.send_message(
                "This paginator isn't for you.", ephemeral=True
            )
            return False
        return True

    def _update_buttons(self) -> None:
        self.first_btn.disabled = self.current == 0
        self.prev_btn.disabled  = self.current == 0
        self.next_btn.disabled  = self.current == len(self.pages) - 1
        self.last_btn.disabled  = self.current == len(self.pages) - 1
        self.page_counter.label = f"{self.current + 1}/{len(self.pages)}"

    async def _go_to(
        self,
        interaction: discord.Interaction,
        index:       int,
    ) -> None:
        self.current = index
        self._update_buttons()
        await interaction.response.edit_message(
            embed=self.pages[self.current],
            view=self,
        )

    @discord.ui.button(emoji="⏮", style=discord.ButtonStyle.grey, row=0)
    async def first_btn(
        self,
        interaction: discord.Interaction,
        button:      discord.ui.Button,
    ) -> None:
        await self._go_to(interaction, 0)

    @discord.ui.button(emoji="◀", style=discord.ButtonStyle.blurple, row=0)
    async def prev_btn(
        self,
        interaction: discord.Interaction,
        button:      discord.ui.Button,
    ) -> None:
        await self._go_to(interaction, self.current - 1)

    @discord.ui.button(label="1/1", style=discord.ButtonStyle.grey, disabled=True, row=0)
    async def page_counter(
        self,
        interaction: discord.Interaction,
        button:      discord.ui.Button,
    ) -> None:
        await interaction.response.defer()

    @discord.ui.button(emoji="▶", style=discord.ButtonStyle.blurple, row=0)
    async def next_btn(
        self,
        interaction: discord.Interaction,
        button:      discord.ui.Button,
    ) -> None:
        await self._go_to(interaction, self.current + 1)

    @discord.ui.button(emoji="⏭", style=discord.ButtonStyle.grey, row=0)
    async def last_btn(
        self,
        interaction: discord.Interaction,
        button:      discord.ui.Button,
    ) -> None:
        await self._go_to(interaction, len(self.pages) - 1)

    @discord.ui.button(emoji="⏹", style=discord.ButtonStyle.red, row=1)
    async def stop_btn(
        self,
        interaction: discord.Interaction,
        button:      discord.ui.Button,
    ) -> None:
        for child in self.children:
            child.disabled = True  # type: ignore[union-attr]
        await interaction.response.edit_message(view=self)
        self.stop()