import discord
from discord import app_commands
import random
import asyncio
import sqlite3
import json
from datetime import datetime
import logging

# 로깅 설정
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

# 봇 설정
intents = discord.Intents.default()
intents.message_content = True
client = discord.Client(intents=intents)
tree = app_commands.CommandTree(client)

# 글로벌 게임 상태 저장
games = {}

# SQLite 데이터베이스 초기화
def init_db():
    conn = sqlite3.connect('buckshot_games.db')
    c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS games (
        channel_id INTEGER PRIMARY KEY,
        player1_id INTEGER,
        player2_id INTEGER,
        hp TEXT,
        chamber TEXT,
        items TEXT,
        current_turn INTEGER,
        knife_active TEXT,
        handcuff_active TEXT,
        round INTEGER,
        scores TEXT,
        last_message_id INTEGER,
        show_chamber INTEGER
    )''')
    conn.commit()
    conn.close()

init_db()

class ItemSelectView(discord.ui.View):
    def __init__(self, game, items, opponent_id, interaction):
        super().__init__(timeout=60)
        self.game = game
        self.opponent_id = opponent_id
        self.interaction = interaction
        select = discord.ui.Select(
            placeholder="사용할 아이템을 선택하세요",
            options=[
                discord.SelectOption(label=item, value=item) for item in items
            ]
        )
        select.callback = self.item_select_callback
        self.add_item(select)

    async def item_select_callback(self, interaction: discord.Interaction):
        if interaction.user.id != self.interaction.user.id:
            await interaction.response.send_message("당신은 이 선택을 할 수 없습니다!", ephemeral=True)
            return
        item = interaction.data["values"][0]
        if item == "주사기" and self.game.items[self.opponent_id]:
            opponent_items = self.game.items[self.opponent_id]
            steal_select = discord.ui.Select(
                placeholder="훔칠 아이템을 선택하세요",
                options=[
                    discord.SelectOption(label=opponent_item, value=f"{opponent_item}_{idx}")
                    for idx, opponent_item in enumerate(opponent_items)
                ]
            )
            async def steal_select_callback(steal_interaction: discord.Interaction):
                if steal_interaction.user.id != interaction.user.id:
                    await steal_interaction.response.send_message("당신은 이 선택을 할 수 없습니다!", ephemeral=True)
                    return
                stolen_value = steal_interaction.data["values"][0]
                stolen_item = stolen_value.split("_")[0]
                self.game.items[self.opponent_id].remove(stolen_item)
                result, continue_turn = self.game.use_item(interaction.user.id, stolen_item, self.opponent_id)
                await self.update_game_message(
                    steal_interaction,
                    f"💉 주사기: {stolen_item}을(를) 훔쳐 사용! {result}",
                    stolen_item in ["맥주", "인버터"],
                    continue_turn
                )
            steal_select.callback = steal_select_callback
            steal_view = discord.ui.View()
            steal_view.add_item(steal_select)
            await interaction.response.send_message("훔칠 아이템을 선택하세요:", view=steal_view, ephemeral=True)
        else:
            result, continue_turn = self.game.use_item(interaction.user.id, item, self.opponent_id)
            if item == "돋보기":
                await interaction.response.send_message(result, ephemeral=True)
                await self.update_game_message(
                    interaction,
                    f"{interaction.user.display_name}이(가) 돋보기를 사용했습니다.",
                    False,
                    continue_turn
                )
            else:
                await self.update_game_message(
                    interaction,
                    result,
                    item in ["맥주", "인버터"],
                    continue_turn
                )

    async def update_game_message(self, interaction, result_message, force_show_chamber, continue_turn):
        game = self.game
        opponent = game.player2 if interaction.user.id == game.player1.id else game.player1
        current_player = game.get_player(game.current_turn)
        embed = discord.Embed(
            title=f"벅샷 룰렛 🔫 | 라운드 {game.round} | {current_player.display_name}의 턴",
            description=f"{game.player1.mention} vs {game.player2.mention}",
            color=discord.Color.red() if game.current_turn == game.player1.id else discord.Color.blue()
        )
        embed.set_thumbnail(url="https://i.imgur.com/9kXz6rT.png")
        embed.add_field(name="아이템 사용", value=result_message, inline=False)
        embed.add_field(name=f"{game.player1.display_name} 체력", value=f"❤️ {game.hp[game.player1.id]}", inline=True)
        embed.add_field(name=f"{game.player2.display_name} 체력", value=f"❤️ {game.hp[game.player2.id]}", inline=True)
        if game.show_chamber or force_show_chamber:
            embed.add_field(name="탄환", value=game.get_chamber_info(), inline=True)
        embed.add_field(
            name=f"{game.player1.display_name} 아이템",
            value=", ".join(game.items[game.player1.id]) or "없음",
            inline=False
        )
        embed.add_field(
            name=f"{game.player2.display_name} 아이템",
            value=", ".join(game.items[game.player2.id]) or "없음",
            inline=False
        )
        embed.add_field(
            name="스코어",
            value=(
                f"{game.player1.display_name}: {game.scores[game.player1.id]} | "
                f"{game.player2.display_name}: {game.scores[game.player2.id]}"
            ),
            inline=True
        )
        embed.set_footer(text=f"마지막 업데이트: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        game.show_chamber = False  # 턴 종료 후 탄환 정보 숨김

        if not continue_turn:  # 약으로 패배 시
            game.scores[opponent.id] += 1
            game_end = game.check_game_end()
            if game_end:
                embed.add_field(name="게임 종료", value=game_end, inline=False)
                view = discord.ui.View()
                game.save_game(self.interaction.channel_id, clear=True)
                if self.interaction.channel_id in games:
                    del games[self.interaction.channel_id]
            else:
                game.start_new_round()
                current_player = game.get_player(game.current_turn)
                embed = discord.Embed(
                    title=f"벅샷 룰렛 🔫 | 라운드 {game.round} | {current_player.display_name}의 턴",
                    description=f"{game.player1.mention} vs {game.player2.mention}",
                    color=discord.Color.red() if game.current_turn == game.player1.id else discord.Color.blue()
                )
                embed.set_thumbnail(url="https://i.imgur.com/9kXz6rT.png")
                embed.add_field(
                    name="새 라운드",
                    value=f"라운드 {game.round} 시작! 체력, 아이템, 탄환이 초기화되었습니다.",
                    inline=False
                )
                embed.add_field(name=f"{game.player1.display_name} 체력", value=f"❤️ {game.hp[game.player1.id]}", inline=True)
                embed.add_field(name=f"{game.player2.display_name} 체력", value=f"❤️ {game.hp[game.player2.id]}", inline=True)
                if game.show_chamber:
                    embed.add_field(name="탄환", value=game.get_chamber_info(), inline=True)
                embed.add_field(
                    name=f"{game.player1.display_name} 아이템",
                    value=", ".join(game.items[game.player1.id]) or "없음",
                    inline=False
                )
                embed.add_field(
                    name=f"{game.player2.display_name} 아이템",
                    value=", ".join(game.items[game.player2.id]) or "없음",
                    inline=False
                )
                embed.add_field(
                    name="스코어",
                    value=(
                        f"{game.player1.display_name}: {game.scores[game.player1.id]} | "
                        f"{game.player2.display_name}: {game.scores[game.player2.id]}"
                    ),
                    inline=True
                )
                embed.set_footer(text=f"마지막 업데이트: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
                view = discord.ui.View(timeout=300)
                view.add_item(discord.ui.Button(
                    label="자신 쏘기",
                    style=discord.ButtonStyle.red,
                    emoji="🔫",
                    custom_id="shoot_self"
                ))
                view.add_item(discord.ui.Button(
                    label="상대 쏘기",
                    style=discord.ButtonStyle.green,
                    emoji="🎯",
                    custom_id="shoot_opponent"
                ))
                view.add_item(discord.ui.Button(
                    label="아이템 사용",
                    style=discord.ButtonStyle.blurple,
                    emoji="🧪",
                    custom_id="use_item"
                ))

        if game.last_message:
            try:
                await game.last_message.delete()
            except discord.NotFound:
                pass
        try:
            game.last_message = await interaction.response.send_message(embed=embed, view=view)
            game.save_game(self.interaction.channel_id, last_message_id=game.last_message.id if game.last_message else None)
        except Exception as e:
            logging.error(f"메시지 전송 실패: {e}")
            await interaction.response.send_message("메시지 전송에 실패했습니다. 다시 시도해주세요.", ephemeral=True)

class BuckshotGame:
    def __init__(self, player1, player2, channel_id=None):
        if player1 is None or player2 is None:
            raise ValueError("플레이어 객체가 유효하지 않습니다.")
        self.player1 = player1
        self.player2 = player2
        initial_hp = random.randint(2, 4)
        self.hp = {player1.id: initial_hp, player2.id: initial_hp}
        self.chamber = []
        self.items = {player1.id: [], player2.id: []}
        self.current_turn = player1.id
        self.knife_active = {player1.id: False, player2.id: False}
        self.handcuff_active = {player1.id: 0, player2.id: 0}
        self.round = 1
        self.scores = {player1.id: 0, player2.id: 0}
        self.last_message = None
        self.channel_id = channel_id
        self.show_chamber = True  # 초기 장전 시 탄환 정보 표시
        self.all_items = [
            "맥주", "돋보기", "담배", "칼", "수갑",
            "버너폰", "약", "인버터", "주사기", "잼머"
        ]
        self.load_chamber()
        self.assign_items(initial=True)
        logging.info(f"게임 시작: {player1.display_name} HP={initial_hp}, {player2.display_name} HP={initial_hp}")

    def save_game(self, channel_id, last_message_id=None, clear=False):
        conn = sqlite3.connect('buckshot_games.db')
        c = conn.cursor()
        if clear:
            c.execute("DELETE FROM games WHERE channel_id = ?", (channel_id,))
        else:
            c.execute('''INSERT OR REPLACE INTO games (
                channel_id, player1_id, player2_id, hp, chamber, items, current_turn,
                knife_active, handcuff_active, round, scores, last_message_id, show_chamber
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)''', (
                channel_id, self.player1.id, self.player2.id,
                json.dumps(self.hp), json.dumps(self.chamber), json.dumps(self.items),
                self.current_turn, json.dumps(self.knife_active), json.dumps(self.handcuff_active),
                self.round, json.dumps(self.scores), last_message_id, int(self.show_chamber)
            ))
        conn.commit()
        conn.close()

    @staticmethod
    async def load_game(channel_id, client):
        conn = sqlite3.connect('buckshot_games.db')
        c = conn.cursor()
        c.execute("SELECT * FROM games WHERE channel_id = ?", (channel_id,))
        row = c.fetchone()
        if not row:
            conn.close()
            return None
        try:
            player1 = await client.fetch_user(row[1])
            player2 = await client.fetch_user(row[2])
            if player1 is None or player2 is None:
                logging.warning(f"유저 조회 실패: player1_id={row[1]}, player2_id={row[2]}")
                c.execute("DELETE FROM games WHERE channel_id = ?", (channel_id,))
                conn.commit()
                conn.close()
                return None
        except discord.errors.NotFound:
            logging.warning(f"유저를 찾을 수 없음: player1_id={row[1]}, player2_id={row[2]}")
            c.execute("DELETE FROM games WHERE channel_id = ?", (channel_id,))
            conn.commit()
            conn.close()
            return None
        game = BuckshotGame(player1, player2, channel_id)
        game.hp = json.loads(row[3])
        game.chamber = json.loads(row[4])
        items = json.loads(row[5])
        game.items = {
            player1.id: items.get(str(player1.id), [])[:4],
            player2.id: items.get(str(player2.id), [])[:4]
        }
        game.current_turn = row[6]
        game.knife_active = json.loads(row[7])
        game.handcuff_active = json.loads(row[8])
        game.round = row[9]
        game.scores = json.loads(row[10])
        game.show_chamber = bool(row[12])
        logging.info(
            f"게임 로드: {player1.display_name} 아이템={len(game.items[player1.id])}, "
            f"{player2.display_name} 아이템={len(game.items[player2.id])}, show_chamber={game.show_chamber}"
        )
        conn.close()
        return game

    def load_chamber(self):
        total_bullets = min(2 + self.round * 2, 8)
        live = random.randint(1, total_bullets // 2 + 1)
        blank = total_bullets - live
        self.chamber = ["live"] * live + ["blank"] * blank
        random.shuffle(self.chamber)
        self.assign_items(initial=False)
        self.show_chamber = True  # 재장전 시 탄환 정보 표시
        logging.info(
            f"탄환 장전: 라운드 {self.round}, 실탄 {self.chamber.count('live')}발, "
            f"공포탄 {self.chamber.count('blank')}발"
        )
        return (
            f"샷건이 새로운 탄환으로 장전되었습니다! "
            f"🔴 실탄: {self.chamber.count('live')}발 | 🔵 공포탄: {self.chamber.count('blank')}발"
        )

    def get_chamber_info(self):
        live_count = self.chamber.count("live")
        blank_count = self.chamber.count("blank")
        return f"🔴 실탄: {live_count}발 | 🔵 공포탄: {blank_count}발"

    def assign_items(self, initial=False):
        item_count = random.choice([2, 4]) if initial else 2
        for player_id in [self.player1.id, self.player2.id]:
            self.items[player_id] = random.sample(self.all_items, item_count)
            self.items[player_id] = self.items[player_id][:4]
            logging.info(
                f"플레이어 {self.get_player(player_id).display_name} 아이템: "
                f"{self.items[player_id]} (총 {len(self.items[player_id])}개)"
            )

    def start_new_round(self):
        self.round += 1
        initial_hp = random.randint(2, 4)
        self.hp = {self.player1.id: initial_hp, self.player2.id: initial_hp}
        self.chamber = []
        self.knife_active = {self.player1.id: False, self.player2.id: False}
        self.handcuff_active = {self.player1.id: 0, self.player2.id: 0}
        self.items = {self.player1.id: [], self.player2.id: []}
        self.show_chamber = True
        self.load_chamber()
        self.assign_items(initial=True)
        self.current_turn = self.player1.id if self.round % 2 == 1 else self.player2.id
        logging.info(
            f"새 라운드 {self.round} 시작, 첫 턴: {self.get_player(self.current_turn).display_name}, "
            f"HP={initial_hp}"
        )

    def shoot(self, shooter_id, target_id):
        if not self.chamber:
            reload_message = self.load_chamber()
            return None, False, 0, reload_message, False
        bullet = self.chamber.pop(0)
        extra_turn = False
        damage = 2 if self.knife_active[shooter_id] else 1
        self.knife_active[shooter_id] = False
        handcuff_used = self.handcuff_active[shooter_id] > 0
        reload_message = None
        reload_reason = None

        if bullet == "live":
            old_hp = self.hp[target_id]
            self.hp[target_id] -= damage
            logging.info(
                f"실탄 발사: {self.get_player(shooter_id).display_name} -> "
                f"{self.get_player(target_id).display_name}, 데미지: {damage}, HP: {old_hp} -> {self.hp[target_id]}"
            )
        elif target_id == shooter_id:
            extra_turn = True
            logging.info(
                f"공포탄 발사: {self.get_player(shooter_id).display_name} -> 자신, 턴 유지"
            )

        # 재장전 조건 확인
        live_count = self.chamber.count("live")
        blank_count = self.chamber.count("blank")
        if live_count >= 2:
            reload_reason = f"실탄 {live_count}개 남음"
            reload_message = self.load_chamber()
        elif blank_count >= 2:
            reload_reason = f"공포탄 {blank_count}개 남음"
            reload_message = self.load_chamber()
        if reload_reason:
            logging.info(f"재장전 트리거: {reload_reason}, 새 탄환: {self.chamber}")

        return bullet, extra_turn, damage, reload_message, handcuff_used

    def use_item(self, user_id, item, opponent_id=None):
        if item not in self.items[user_id]:
            return "아이템을 가지고 있지 않습니다!", False
        self.items[user_id].remove(item)
        if item == "맥주":
            if self.chamber:
                bullet = self.chamber.pop(0)
                live_count = self.chamber.count("live")
                blank_count = self.chamber.count("blank")
                reload_message = None
                if live_count >= 2 or blank_count >= 2:
                    reload_message = self.load_chamber()
                    return (
                        f"🍺 맥주: {'🔴 실탄' if bullet == 'live' else '🔵 공포탄'}을 배출했습니다! "
                        f"재장전: {reload_message}"
                    ), True
                return (
                    f"🍺 맥주: {'🔴 실탄' if bullet == 'live' else '🔵 공포탄'}을 배출했습니다!"
                ), True
            return "🔄 탄환이 없습니다!", True
        elif item == "돋보기":
            if self.chamber:
                return (
                    f"🔍 돋보기: 다음 탄환은 {'🔴 실탄' if self.chamber[0] == 'live' else '🔵 공포탄'}입니다!"
                ), True
            return "🔄 탄환이 없습니다!", True
        elif item == "담배":
            if self.hp[user_id] < 6:
                old_hp = self.hp[user_id]
                self.hp[user_id] += 1
                return (
                    f"🚬 담배: 체력 1 회복! HP: {old_hp} → {self.hp[user_id]}"
                ), True
            return "🚬 담배: 이미 최대 체력입니다!", True
        elif item == "칼":
            self.knife_active[user_id] = True
            return "🪚 칼: 다음 샷 데미지 2배!", True
        elif item == "수갑" or item == "잼머":
            self.handcuff_active[opponent_id] += 1
            return (
                f"⛓ {'수갑' if item == '수갑' else '잼머'}: 상대의 다음 턴을 건너뜁니다!"
            ), True
        elif item == "주사기":
            if opponent_id and self.items[opponent_id]:
                return (
                    "💉 주사기: 상대의 아이템을 선택해 훔쳐 즉시 사용합니다."
                ), True
            return "💉 주사기: 상대에게 훔칠 아이템이 없습니다!", True
        elif item == "버너폰":
            if self.chamber:
                idx = random.randint(0, len(self.chamber) - 1)
                bullet = self.chamber[idx]
                return (
                    f"📱 버너폰: {idx + 1}번째 탄환은 {'🔴 실탄' if bullet == 'live' else '🔵 공포탄'}입니다!"
                ), True
            return "🔄 탄환이 없습니다!", True
        elif item == "약":
            if random.random() < 0.4:
                old_hp = self.hp[user_id]
                self.hp[user_id] = min(self.hp[user_id] + 2, 6)
                return (
                    f"💊 약: 2HP 회복! HP: {old_hp} → {self.hp[user_id]}"
                ), True
            else:
                old_hp = self.hp[user_id]
                self.hp[user_id] -= 1
                if self.hp[user_id] <= 0:
                    return (
                        f"💊 약: 1HP 손실! "
                        f"{self.get_player(user_id).display_name} 패배!"
                    ), False
                return (
                    f"💊 약: 1HP 손실! HP: {old_hp} → {self.hp[user_id]}"
                ), True
        elif item == "인버터":
            if self.chamber and len(self.chamber) > 1:
                self.chamber[0], self.chamber[1] = self.chamber[1], self.chamber[0]
                live_count = self.chamber.count("live")
                blank_count = self.chamber.count("blank")
                if live_count >= 2 or blank_count >= 2:
                    reload_message = self.load_chamber()
                    return (
                        f"🔄 인버터: 현재 탄환과 다음 탄환의 위치가 바뀌었습니다! "
                        f"재장전: {reload_message}"
                    ), True
                return (
                    f"🔄 인버터: 현재 탄환과 다음 탄환의 위치가 바뀌었습니다!"
                ), True
            return "🔄 인버터: 사용할 수 없습니다!", True
        return "아이템 사용 실패!", True

    def get_player(self, player_id):
        return self.player1 if player_id == self.player1.id else self.player2

    def switch_turn(self):
        opponent_id = self.player2.id if self.current_turn == self.player1.id else self.player1.id
        if self.handcuff_active[opponent_id] > 0:
            self.handcuff_active[opponent_id] -= 1
            logging.info(f"수갑 효과: {self.get_player(opponent_id).display_name} 턴 스킵")
            return
        self.current_turn = opponent_id
        self.show_chamber = False  # 턴 변경 시 탄환 정보 숨김
        logging.info(f"턴 변경: {self.get_player(self.current_turn).display_name}")

    def check_game_end(self):
        if self.scores[self.player1.id] >= 2:
            return (
                f"{self.player1.display_name} 최종 승리! 🏆 "
                f"({self.scores[self.player1.id]}:{self.scores[self.player2.id]})"
            )
        elif self.scores[self.player2.id] >= 2:
            return (
                f"{self.player2.display_name} 최종 승리! 🏆 "
                f"({self.scores[self.player1.id]}:{self.scores[self.player2.id]})"
            )
        return None

@tree.command(name="buckshot", description="다른 유저와 벅샷 룰렛 대결을 시작합니다!")
@app_commands.describe(opponent="대결할 상대를 선택하세요")
async def buckshot(interaction: discord.Interaction, opponent: discord.Member):
    if opponent == interaction.user:
        await interaction.response.send_message("자신과 대결할 수 없습니다!", ephemeral=True)
        return
    if opponent.bot:
        await interaction.response.send_message("봇과 대결할 수 없습니다!", ephemeral=True)
        return
    try:
        game = await BuckshotGame.load_game(interaction.channel_id, client)
        if game:
            await interaction.response.send_message("이 채널에서 이미 게임이 진행 중입니다!", ephemeral=True)
            return
    except Exception as e:
        logging.error(f"게임 로드 실패: {e}")
        conn = sqlite3.connect('buckshot_games.db')
        c = conn.cursor()
        c.execute("DELETE FROM games WHERE channel_id = ?", (interaction.channel_id,))
        conn.commit()
        conn.close()

    game = BuckshotGame(interaction.user, opponent, interaction.channel_id)
    games[interaction.channel_id] = game
    current_player = game.get_player(game.current_turn)
    embed = discord.Embed(
        title=f"벅샷 룰렛 🔫 | 라운드 {game.round} | {current_player.display_name}의 턴",
        description=f"{interaction.user.mention} vs {opponent.mention}",
        color=discord.Color.red() if game.current_turn == interaction.user.id else discord.Color.blue()
    )
    embed.set_thumbnail(url="https://i.imgur.com/9kXz6rT.png")
    embed.add_field(name=f"{interaction.user.display_name} 체력", value=f"❤️ {game.hp[interaction.user.id]}", inline=True)
    embed.add_field(name=f"{opponent.display_name} 체력", value=f"❤️ {game.hp[opponent.id]}", inline=True)
    if game.show_chamber:
        embed.add_field(name="탄환", value=game.get_chamber_info(), inline=True)
    embed.add_field(
        name=f"{interaction.user.display_name} 아이템",
        value=", ".join(game.items[interaction.user.id]) or "없음",
        inline=False
    )
    embed.add_field(
        name=f"{opponent.display_name} 아이템",
        value=", ".join(game.items[opponent.id]) or "없음",
        inline=False
    )
    embed.add_field(
        name="스코어",
        value=(
            f"{interaction.user.display_name}: {game.scores[interaction.user.id]} | "
            f"{opponent.display_name}: {game.scores[opponent.id]}"
        ),
        inline=True
    )
    embed.set_footer(text=f"마지막 업데이트: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    game.show_chamber = False  # 초기 표시 후 숨김

    view = discord.ui.View(timeout=300)
    view.add_item(discord.ui.Button(
        label="자신 쏘기",
        style=discord.ButtonStyle.red,
        emoji="🔫",
        custom_id="shoot_self"
    ))
    view.add_item(discord.ui.Button(
        label="상대 쏘기",
        style=discord.ButtonStyle.green,
        emoji="🎯",
        custom_id="shoot_opponent"
    ))
    view.add_item(discord.ui.Button(
        label="아이템 사용",
        style=discord.ButtonStyle.blurple,
        emoji="🧪",
        custom_id="use_item"
    ))

    invite_embed = discord.Embed(
        title="벅샷 룰렛 초대 🔫",
        description=f"{opponent.mention}, {interaction.user.mention}이(가) 대결을 요청했습니다! 수락하시겠습니까?",
        color=discord.Color.dark_grey()
    )
    invite_embed.set_image(url="https://i.imgur.com/3QfY7aP.png")
    invite_view = discord.ui.View()
    accept_button = discord.ui.Button(label="수락", style=discord.ButtonStyle.green, emoji="✅")
    async def accept_callback(button_interaction: discord.Interaction):
        if button_interaction.user.id != opponent.id:
            await button_interaction.response.send_message("당신은 초대를 수락할 수 없습니다!", ephemeral=True)
            return
        if game.last_message:
            try:
                await game.last_message.delete()
            except discord.NotFound:
                pass
        try:
            game.last_message = await button_interaction.response.send_message(embed=embed, view=view)
            game.save_game(interaction.channel_id, last_message_id=game.last_message.id if game.last_message else None)
            invite_view.clear_items()
        except Exception as e:
            logging.error(f"초대 수락 메시지 전송 실패: {e}")
            await button_interaction.response.send_message("메시지 전송에 실패했습니다. 다시 시도해주세요.", ephemeral=True)

    accept_button.callback = accept_callback
    invite_view.add_item(accept_button)

    reject_button = discord.ui.Button(label="거절", style=discord.ButtonStyle.red, emoji="❌")
    async def reject_callback(button_interaction: discord.Interaction):
        if button_interaction.user.id != opponent.id:
            await button_interaction.response.send_message("당신은 초대를 거절할 수 없습니다!", ephemeral=True)
            return
        if game.last_message:
            try:
                await game.last_message.delete()
            except discord.NotFound:
                pass
        await button_interaction.response.send_message(
            f"{opponent.display_name}이(가) 초대를 거절했습니다!",
            ephemeral=False
        )
        invite_view.clear_items()
        game.save_game(interaction.channel_id, clear=True)
        if interaction.channel_id in games:
            del games[interaction.channel_id]

    reject_button.callback = reject_callback
    invite_view.add_item(reject_button)

    await interaction.response.send_message(embed=invite_embed, view=invite_view)

@client.event
async def on_interaction(interaction: discord.Interaction):
    if not interaction.data or 'custom_id' not in interaction.data:
        return
    custom_id = interaction.data['custom_id']
    game = games.get(interaction.channel_id)
    if not game:
        game = await BuckshotGame.load_game(interaction.channel_id, client)
        if game:
            games[interaction.channel_id] = game
        else:
            return
    if interaction.user.id != game.current_turn:
        await interaction.response.send_message("당신의 턴이 아닙니다!", ephemeral=True)
        return
    opponent = game.player2 if interaction.user.id == game.player1.id else game.player1
    target_id = opponent.id if custom_id == "shoot_opponent" else interaction.user.id
    embed = discord.Embed(
        title=f"벅샷 룰렛 🔫 | 라운드 {game.round} | {game.get_player(game.current_turn).display_name}의 턴",
        description=f"{game.player1.mention} vs {game.player2.mention}",
        color=discord.Color.red() if game.current_turn == game.player1.id else discord.Color.blue()
    )
    embed.set_thumbnail(url="https://i.imgur.com/9kXz6rT.png")
    view = discord.ui.View(timeout=300)
    view.add_item(discord.ui.Button(
        label="자신 쏘기",
        style=discord.ButtonStyle.red,
        emoji="🔫",
        custom_id="shoot_self"
    ))
    view.add_item(discord.ui.Button(
        label="상대 쏘기",
        style=discord.ButtonStyle.green,
        emoji="🎯",
        custom_id="shoot_opponent"
    ))
    view.add_item(discord.ui.Button(
        label="아이템 사용",
        style=discord.ButtonStyle.blurple,
        emoji="🧪",
        custom_id="use_item"
    ))

    if custom_id in ["shoot_self", "shoot_opponent"]:
        bullet, extra_turn, damage, reload_message, handcuff_used = game.shoot(
            interaction.user.id, target_id
        )
        show_chamber = bool(reload_message)
        if reload_message:
            embed.add_field(name="장전", value=reload_message, inline=False)
        else:
            target_name = game.get_player(target_id).display_name
            if bullet == "live":
                old_hp = game.hp[target_id] + damage
                result_text = (
                    f"💥 실탄 🔴! {interaction.user.display_name}이(가) {target_name}에게 "
                    f"{damage} 데미지! HP: {old_hp} → {game.hp[target_id]}"
                )
            else:
                result_text = (
                    f"🔵 공포탄! {interaction.user.display_name}이(가) {target_name}에게 "
                    f"쐈으나 피해 없음."
                )
            embed.add_field(name="발사 결과", value=result_text, inline=False)
            if handcuff_used:
                embed.add_field(
                    name="수갑 효과",
                    value=f"{interaction.user.display_name}이(가) 수갑으로 턴을 유지했습니다!",
                    inline=False
                )
        embed.add_field(name=f"{game.player1.display_name} 체력", value=f"❤️ {game.hp[game.player1.id]}", inline=True)
        embed.add_field(name=f"{game.player2.display_name} 체력", value=f"❤️ {game.hp[game.player2.id]}", inline=True)
        if show_chamber:
            embed.add_field(name="탄환", value=game.get_chamber_info(), inline=True)
        embed.add_field(
            name=f"{game.player1.display_name} 아이템",
            value=", ".join(game.items[game.player1.id]) or "없음",
            inline=False
        )
        embed.add_field(
            name=f"{game.player2.display_name} 아이템",
            value=", ".join(game.items[game.player2.id]) or "없음",
            inline=False
        )
        embed.add_field(
            name="스코어",
            value=(
                f"{game.player1.display_name}: {game.scores[game.player1.id]} | "
                f"{game.player2.display_name}: {game.scores[game.player2.id]}"
            ),
            inline=True
        )
        embed.set_footer(text=f"마지막 업데이트: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        game.show_chamber = False  # 발사 후 탄환 정보 숨김

        if game.hp[target_id] <= 0:
            winner_id = interaction.user.id if custom_id == "shoot_opponent" else opponent.id
            game.scores[winner_id] += 1
            embed.add_field(
                name="라운드 종료",
                value=f"{game.get_player(winner_id).display_name}이(가) 라운드 {game.round} 승리!",
                inline=False
            )
            game_end = game.check_game_end()
            if game_end:
                embed.add_field(name="게임 종료", value=game_end, inline=False)
                view.clear_items()
                game.save_game(interaction.channel_id, clear=True)
                if interaction.channel_id in games:
                    del games[interaction.channel_id]
            else:
                game.start_new_round()
                current_player = game.get_player(game.current_turn)
                embed = discord.Embed(
                    title=f"벅샷 룰렛 🔫 | 라운드 {game.round} | {current_player.display_name}의 턴",
                    description=f"{game.player1.mention} vs {game.player2.mention}",
                    color=discord.Color.red() if game.current_turn == game.player1.id else discord.Color.blue()
                )
                embed.set_thumbnail(url="https://i.imgur.com/9kXz6rT.png")
                embed.add_field(
                    name="새 라운드",
                    value=f"라운드 {game.round} 시작! 체력, 아이템, 탄환이 초기화되었습니다.",
                    inline=False
                )
                embed.add_field(name=f"{game.player1.display_name} 체력", value=f"❤️ {game.hp[game.player1.id]}", inline=True)
                embed.add_field(name=f"{game.player2.display_name} 체력", value=f"❤️ {game.hp[game.player2.id]}", inline=True)
                if game.show_chamber:
                    embed.add_field(name="탄환", value=game.get_chamber_info(), inline=True)
                embed.add_field(
                    name=f"{game.player1.display_name} 아이템",
                    value=", ".join(game.items[game.player1.id]) or "없음",
                    inline=False
                )
                embed.add_field(
                    name=f"{game.player2.display_name} 아이템",
                    value=", ".join(game.items[game.player2.id]) or "없음",
                    inline=False
                )
                embed.add_field(
                    name="스코어",
                    value=(
                        f"{game.player1.display_name}: {game.scores[game.player1.id]} | "
                        f"{game.player2.display_name}: {game.scores[game.player2.id]}"
                    ),
                    inline=True
                )
                embed.set_footer(text=f"마지막 업데이트: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
                game.show_chamber = False
        else:
            if not extra_turn and not handcuff_used:
                game.switch_turn()
            current_player = game.get_player(game.current_turn)
            embed.title = (
                f"벅샷 룰렛 🔫 | 라운드 {game.round} | {current_player.display_name}의 턴"
            )
            embed.color = discord.Color.red() if game.current_turn == game.player1.id else discord.Color.blue()

        if game.last_message:
            try:
                await game.last_message.delete()
            except discord.NotFound:
                pass
        try:
            game.last_message = await interaction.response.send_message(embed=embed, view=view)
            game.save_game(interaction.channel_id, last_message_id=game.last_message.id if game.last_message else None)
        except Exception as e:
            logging.error(f"인터랙션 메시지 전송 실패: {e}")
            await interaction.response.send_message("메시지 전송에 실패했습니다. 다시 시도해주세요.", ephemeral=True)

    elif custom_id == "use_item":
        items = game.items[interaction.user.id]
        if not items:
            await interaction.response.send_message("사용 가능한 아이템이 없습니다!", ephemeral=True)
            return
        await interaction.response.send_message(
            "아이템을 선택하세요:", view=ItemSelectView(game, items, opponent.id, interaction), ephemeral=True
        )

@tree.command(name="items", description="벅샷 룰렛 게임의 아이템 설명을 확인합니다.")
async def items(interaction: discord.Interaction):
    embed = discord.Embed(
        title="벅샷 룰렛 아이템 설명 🧪",
        description="각 아이템의 효과를 확인하세요!",
        color=discord.Color.purple()
    )
    embed.set_thumbnail(url="https://i.imgur.com/5mV8Z2j.png")
    item_descriptions = {
        "맥주": "샷건에서 현재 탄환을 배출하고 실탄인지 공포탄인지 확인합니다.",
        "돋보기": "다음 탄환의 종류(실탄/공포탄)를 확인합니다.",
        "담배": "체력을 1 회복합니다. 최대 체력은 6입니다.",
        "칼": "다음 샷의 데미지를 2배로 만듭니다.",
        "수갑": "상대의 다음 턴을 건너뜁니다 (중첩 가능).",
        "주사기": "상대의 아이템 하나를 선택해 훔쳐 즉시 사용합니다.",
        "버너폰": "샷건에 남은 무작위 탄환의 종류를 확인합니다.",
        "약": "40% 확률로 체력 2 회복, 60% 확률로 체력 1 손실.",
        "인버터": "현재 탄환과 다음 탄환의 위치를 교환합니다.",
        "잼머": "상대의 다음 턴을 건너뜁니다 (수갑과 동일)."
    }
    for item, description in item_descriptions.items():
        embed.add_field(name=item, value=description, inline=False)
    await interaction.response.send_message(embed=embed, ephemeral=True)

@tree.command(name="reset_game", description="현재 채널의 게임 데이터를 초기화합니다.")
async def reset_game(interaction: discord.Interaction):
    conn = sqlite3.connect('buckshot_games.db')
    c = conn.cursor()
    c.execute("DELETE FROM games WHERE channel_id = ?", (interaction.channel_id,))
    conn.commit()
    conn.close()
    if interaction.channel_id in games:
        del games[interaction.channel_id]
    await interaction.response.send_message("게임 데이터가 초기화되었습니다!", ephemeral=True)

@client.event
async def on_ready():
    print(f'Logged in as {client.user}')
    await tree.sync()
    print("Slash commands synced!")

client.run('')
