import discord
from discord import app_commands
import random
import asyncio
import sqlite3
from uuid import uuid4
import json
import os
import threading

# 디스코드 인텐트 설정
intents = discord.Intents.default()
intents.message_content = True
client = discord.Client(intents=intents)
tree = app_commands.CommandTree(client)

# 동기화 플래그
synced = False

# JSON 파일 동기화를 위한 Lock
json_lock = threading.Lock()

# JSON 파일 관리
USER_JSON_PATH = "user.json"

def init_json():
    """user.json 파일 초기화"""
    with json_lock:
        if not os.path.exists(USER_JSON_PATH):
            with open(USER_JSON_PATH, "w", encoding="utf-8") as f:
                json.dump({}, f)

def save_items_to_json(game_id, player1_id, player2_id, items):
    """아이템을 user.json에 저장"""
    with json_lock:
        try:
            with open(USER_JSON_PATH, "r", encoding="utf-8") as f:
                data = json.load(f)
        except (FileNotFoundError, json.JSONDecodeError):
            data = {}
        
        data[game_id] = {
            str(player1_id): items[player1_id],
            str(player2_id): items[player2_id]
        }
        
        print(f"Saving items for game {game_id}: {data[game_id]}")  # 디버깅 로그
        with open(USER_JSON_PATH, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

def load_items_from_json(game_id, player1_id, player2_id):
    """user.json에서 아이템 로드"""
    with json_lock:
        try:
            with open(USER_JSON_PATH, "r", encoding="utf-8") as f:
                data = json.load(f)
            game_data = data.get(game_id, {})
            return {
                player1_id: game_data.get(str(player1_id), []),
                player2_id: game_data.get(str(player2_id), [])
            }
        except (FileNotFoundError, json.JSONDecodeError, KeyError):
            return {player1_id: [], player2_id: []}

def delete_items_from_json(game_id):
    """게임 종료 시 user.json에서 아이템 데이터 삭제"""
    with json_lock:
        try:
            with open(USER_JSON_PATH, "r", encoding="utf-8") as f:
                data = json.load(f)
        except (FileNotFoundError, json.JSONDecodeError):
            data = {}
        if game_id in data:
            del data[game_id]
            with open(USER_JSON_PATH, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)

# SQLite 데이터베이스 초기화
def init_db():
    try:
        with sqlite3.connect("buckshot.db") as conn:
            c = conn.cursor()
            c.execute("DROP TABLE IF EXISTS games")
            c.execute("DROP TABLE IF EXISTS game_states")
            c.execute("DROP TABLE IF EXISTS player_money")
            c.execute('''CREATE TABLE games (
                game_id TEXT PRIMARY KEY,
                player1_id INTEGER,
                player2_id INTEGER,
                round INTEGER,
                scores TEXT,
                status TEXT,
                prize INTEGER,
                double_or_nothing BOOLEAN
            )''')
            c.execute('''CREATE TABLE game_states (
                game_id TEXT,
                turn INTEGER,
                current_turn_id INTEGER,
                hp TEXT,
                chamber TEXT,
                knife_active TEXT,
                handcuff_active TEXT,
                jammer_active TEXT,
                item_usage TEXT,
                FOREIGN KEY (game_id) REFERENCES games (game_id)
            )''')
            c.execute('''CREATE TABLE player_money (
                player_id INTEGER PRIMARY KEY,
                total_money INTEGER,
                item_usage_history TEXT
            )''')
            conn.commit()
            return True, "데이터베이스가 성공적으로 초기화되었습니다!"
    except sqlite3.Error as e:
        return False, f"데이터베이스 초기화 중 오류 발생: {e}"

init_db()
init_json()

class BuckshotGame:
    def __init__(self, player1, player2, double_or_nothing=False):
        self.conn = sqlite3.connect("buckshot.db")
        self.game_id = str(uuid4())
        self.player1 = player1
        self.player2 = player2
        self.round = 1
        self.scores = {player1.id: 0, player2.id: 0}
        self.prize = 0
        self.last_message = None
        self.status = "pending"
        self.double_or_nothing = double_or_nothing
        self.item_usage = {player1.id: {"담배": 0, "맥주": 0, "주사기": 0}, player2.id: {"담배": 0, "맥주": 0, "주사기": 0}}
        self._init_game_state()
        self._save_to_db()

    def __del__(self):
        self.conn.close()

    def _init_game_state(self):
        if self.round == 1:
            self.hp = {self.player1.id: 2, self.player2.id: 2}
            self.max_hp = 2
            item_count = 2
        elif self.round == 2:
            self.hp = {self.player1.id: 4, self.player2.id: 4}
            self.max_hp = 4
            item_count = 2
        else:
            self.hp = {self.player1.id: 6, self.player2.id: 6}
            self.max_hp = 6
            item_count = 4
        self.chamber = []
        self.current_turn = self.player1.id
        self.knife_active = {self.player1.id: False, self.player2.id: False}
        self.handcuff_active = {self.player1.id: False, self.player2.id: False}
        self.jammer_active = {self.player1.id: False, self.player2.id: False}
        self.items = {self.player1.id: [], self.player2.id: []}
        self.assign_items(initial=True, count=item_count)
        self.load_chamber(skip_items=True)
        self._save_state()

    def assign_items(self, initial=False, count=2):
        """플레이어에게 아이템을 할당하는 메서드"""
        item_pool = ["맥주", "돋보기", "담배", "칼", "수갑", "주사기", "버너폰", "인버터", "재머"]
        if self.double_or_nothing:
            item_pool.append("상한 약")
        
        for player_id in [self.player1.id, self.player2.id]:
            if initial:
                self.items[player_id] = []
            # 현재 플레이어가 이미 가진 아이템 제외
            available_items = [item for item in item_pool if item not in self.items[player_id]]
            if len(available_items) < count:
                count = len(available_items)
            if count > 0:
                new_items = random.sample(available_items, count)
                new_items = list(dict.fromkeys(new_items))  # 중복 아이템 제거
                self.items[player_id].extend(new_items)
                print(f"Assigned items to player {player_id}: {new_items}")  # 디버깅 로그
            self.items[player_id] = self.items[player_id][:8]  # 최대 8개 아이템 제한
        save_items_to_json(self.game_id, self.player1.id, self.player2.id, self.items)

    def load_chamber(self, skip_items=False):
        if self.round == 1:
            live = random.randint(1, 3)
            blank = 2
            item_count = 2
        elif self.round == 2:
            total_bullets = random.randint(2, 8)
            live = random.randint(1, min(4, total_bullets - 1))
            blank = total_bullets - live
            item_count = 2
        else:
            total_bullets = random.randint(2, 8)
            live = random.randint(1, min(4, total_bullets - 1))
            blank = total_bullets - live
            item_count = 4
        self.chamber = ["live"] * live + ["blank"] * blank
        random.shuffle(self.chamber)
        if not skip_items:
            self.assign_items(initial=False, count=item_count)
            self._save_state()
            return (f"샷건이 새로운 탄환으로 장전되었습니다! 🔴 실탄: {self.chamber.count('live')}발 | 🔵 공포탄: {self.chamber.count('blank')}발\n"
                    f"각 플레이어에게 아이템 {item_count}개가 추가되었습니다!")
        self._save_state()
        return f"샷건이 새로운 탄환으로 장전되었습니다! 🔴 실탄: {self.chamber.count('live')}발 | 🔵 공포탄: {self.chamber.count('blank')}발"

    # 나머지 메서드들은 기존 코드와 동일하므로 생략
    # 전체 코드가 필요하면 요청해 주세요!

    def get_chamber_info(self):
        live_count = self.chamber.count("live")
        blank_count = self.chamber.count("blank")
        return f"🔴 실탄: {live_count}발 | 🔵 공포탄: {blank_count}발"

    def get_hp_bar(self, player_id, viewer_id):
        current_hp = self.hp[player_id]
        max_hp = self.max_hp
        if self.round == 3 and current_hp <= 2 and player_id != viewer_id:
            return "???"
        hearts = "❤️" * current_hp
        empty = "⬜" * (max_hp - current_hp)
        return f"{hearts}{empty} ({current_hp}/{max_hp})"

    def get_items(self):
        """아이템을 JSON에서 로드"""
        self.items = load_items_from_json(self.game_id, self.player1.id, self.player2.id)
        return self.items

    def start_new_round(self):
        self.round += 1
        if self.round > 3:
            return False
        self._init_game_state()
        self.current_turn = self.player1.id if self.round % 2 == 1 else self.player2.id
        self._save_to_db()
        return True

    def shoot(self, shooter_id, target_id):
        if not self.chamber:
            reload_message = self.load_chamber()
            return None, False, 0, reload_message, False, False, 0
        bullet = self.chamber.pop(0)
        extra_turn = False
        damage = 2 if self.knife_active[shooter_id] else 1
        knife_used = self.knife_active[shooter_id]
        self.knife_active[shooter_id] = False
        handcuff_used = self.handcuff_active[shooter_id]
        self.handcuff_active[shooter_id] = False
        old_hp = self.hp[target_id]
        if bullet == "live":
            if self.round == 3 and self.hp[target_id] <= 2:
                damage = self.hp[target_id]
                self.hp[target_id] = 0
            else:
                self.hp[target_id] -= damage
        elif target_id == shooter_id:
            extra_turn = True
        reload_message = None
        if self.chamber.count("blank") == 0 and self.chamber.count("live") == 0:
            reload_message = self.load_chamber()
        self._save_state()
        return bullet, extra_turn, damage, reload_message, handcuff_used, knife_used, old_hp

    def use_item(self, user_id, item, opponent_id=None):
        self.get_items()  # 최신 아이템 로드
        if item not in self.items[user_id]:
            return "해당 아이템을 가지고 있지 않습니다!"
        if self.jammer_active.get(opponent_id, False):
            self.jammer_active[opponent_id] = False
            self._save_state()
            return "재머: 상대의 재머로 인해 아이템 사용이 무효화되었습니다!"
        if item == "맥주" and self.chamber:
            self.item_usage[user_id]["맥주"] += 1
            bullet = self.chamber.pop(0)
            self._save_state()
            return f"맥주: {'🔴 실탄' if bullet == 'live' else '🔵 공포탄'}을 배출했습니다!"
        elif item == "돋보기" and self.chamber:
            return f"돋보기: 다음 탄환은 {'🔴 실탄' if self.chamber[0] == 'live' else '🔵 공포탄'}입니다!"
        elif item == "담배":
            if self.round == 3 and self.hp[user_id] <= 2:
                return "담배: 체력 2 이하에서는 회복 불가!"
            if self.hp[user_id] < 4:
                self.item_usage[user_id]["담배"] += 1
                self.hp[user_id] += 1
                self._save_state()
                return "담배: 체력 1 회복!"
            return "담배: 이미 최대 체력입니다!"
        elif item == "칼":
            self.knife_active[user_id] = True
            self._save_state()
            return "칼: 다음 샷 대미지 2배!"
        elif item == "수갑":
            self.handcuff_active[user_id] = True
            self._save_state()
            return "수갑: 다음 상대 샷 후에도 턴을 유지합니다!"
        elif item == "주사기" and opponent_id and self.items[opponent_id]:
            self.item_usage[user_id]["주사기"] += 1
            return "주사기: 상대의 아이템을 선택해 훔쳐 즉시 사용합니다."
        elif item == "버너폰" and self.chamber:
            if len(self.chamber) >= 3:
                bullet_type = "실탄" if self.chamber[-1] == "live" else "공포탄"
                self._save_state()
                return f"버너폰: {len(self.chamber)}번째 탄은 {bullet_type}이야..."
            elif len(self.chamber) == 2:
                self._save_state()
                return "버너폰: 안타깝게... 됐군..."
            else:
                return "버너폰: 잔탄이 너무 적어 사용할 수 없습니다!"
        elif item == "인버터" and self.chamber:
            self.chamber[0] = "live" if self.chamber[0] == "blank" else "blank"
            self._save_state()
            return "인버터: 다음 탄환의 상태가 변경되었습니다!"
        elif item == "상한 약":
            if random.random() < 0.5:
                self.hp[user_id] = min(self.hp[user_id] + 2, self.max_hp)
                self._save_state()
                return "상한 약: 체력 2 회복!"
            else:
                self.hp[user_id] = max(self.hp[user_id] - 1, 0)
                self._save_state()
                return "상한 약: 체력 1 감소!"
        elif item == "재머" and opponent_id:
            self.jammer_active[opponent_id] = True
            self._save_state()
            return "재머: 상대의 다음 아이템 사용을 무효화합니다!"
        return "아이템 사용 실패! 조건이 맞지 않습니다."

    def switch_turn(self):
        self.current_turn = self.player2.id if self.current_turn == self.player1.id else self.player1.id
        self._save_state()

    def check_game_end(self):
        if self.round >= 3:
            if self.scores[self.player1.id] > self.scores[self.player2.id]:
                self.prize = self.calculate_prize(self.player1.id)
                self.update_player_money(self.player1.id, self.prize)
                return f"{self.player1.display_name} 최종 승리! 🏆 상금: ${self.prize:,} ({self.scores[self.player1.id]}:{self.scores[self.player2.id]})"
            elif self.scores[self.player2.id] > self.scores[self.player1.id]:
                self.prize = self.calculate_prize(self.player2.id)
                self.update_player_money(self.player2.id, self.prize)
                return f"{self.player2.display_name} 최종 승리! 🏆 상금: ${self.prize:,} ({self.scores[self.player1.id]}:{self.scores[self.player2.id]})"
            return f"무승부! (${self.prize:,}) ({self.scores[self.player1.id]}:{self.scores[self.player2.id]})"
        return None

    def calculate_prize(self, winner_id):
        base_prize = 70000
        usage = self.item_usage.get(winner_id, {"담배": 0, "맥주": 0, "주사기": 0})
        deductions = (usage["담배"] * 220) + (usage["맥주"] * 495) + (usage["주사기"] * 3000)
        return max(0, base_prize - deductions)

    def update_player_money(self, player_id, prize):
        c = self.conn.cursor()
        c.execute("SELECT total_money, item_usage_history FROM player_money WHERE player_id = ?", (player_id,))
        result = c.fetchone()
        if result:
            total_money, usage_history = result
            usage_history = json.loads(usage_history)
            for item, count in self.item_usage[player_id].items():
                usage_history[item] = usage_history.get(item, 0) + count
            total_money += prize
            c.execute("UPDATE player_money SET total_money = ?, item_usage_history = ? WHERE player_id = ?",
                      (total_money, json.dumps(usage_history), player_id))
        else:
            usage_history = self.item_usage[player_id]
            c.execute("INSERT INTO player_money (player_id, total_money, item_usage_history) VALUES (?, ?, ?)",
                      (player_id, prize, json.dumps(usage_history)))
        self.conn.commit()

    def _save_to_db(self):
        c = self.conn.cursor()
        c.execute('''INSERT OR REPLACE INTO games (game_id, player1_id, player2_id, round, scores, status, prize, double_or_nothing)
                     VALUES (?, ?, ?, ?, ?, ?, ?, ?)''',
                  (self.game_id, self.player1.id, self.player2.id, self.round, json.dumps(self.scores), self.status, self.prize, self.double_or_nothing))
        self.conn.commit()

    def _save_state(self):
        c = self.conn.cursor()
        c.execute('''INSERT OR REPLACE INTO game_states (game_id, turn, current_turn_id, hp, chamber, knife_active, handcuff_active, jammer_active, item_usage)
                     VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)''',
                  (self.game_id, self.round, self.current_turn, json.dumps(self.hp), json.dumps(self.chamber),
                   json.dumps(self.knife_active), json.dumps(self.handcuff_active),
                   json.dumps(self.jammer_active), json.dumps(self.item_usage)))
        self.conn.commit()

    def end_game(self):
        c = self.conn.cursor()
        c.execute("DELETE FROM games WHERE game_id = ?", (self.game_id,))
        c.execute("DELETE FROM game_states WHERE game_id = ?", (self.game_id,))
        self.conn.commit()
        delete_items_from_json(self.game_id)

# 나머지 코드는 기존과 동일 (명령어, 이벤트 핸들러 등)
# 전체 코드가 필요하면 요청해 주세요!

@tree.command(name="buckshot", description="다른 유저와 벅샷 룰렛 대결을 시작합니다!")
@app_commands.describe(opponent="대결할 상대를 선택하세요", mode="게임 모드: Normal 또는 Double or Nothing")
async def buckshot(interaction: discord.Interaction, opponent: discord.Member, mode: str = "Normal"):
    print(f"Received /buckshot command from {interaction.user.id} for opponent {opponent.id} with mode {mode}")
    if opponent == interaction.user:
        await interaction.response.send_message("자신과 대결할 수 없습니다!", ephemeral=True)
        return
    if opponent.bot:
        await interaction.response.send_message("봇과 대결할 수 없습니다!", ephemeral=True)
        return
    if mode not in ["Normal", "Double or Nothing"]:
        await interaction.response.send_message("유효하지 않은 모드입니다! Normal 또는 Double or Nothing을 선택하세요.", ephemeral=True)
        return

    with sqlite3.connect("buckshot.db") as conn:
        c = conn.execute("SELECT game_id FROM games WHERE (player1_id = ? OR player2_id = ?) AND status = 'active'",
                         (interaction.user.id, interaction.user.id))
        if c.fetchone():
            await interaction.response.send_message("이미 진행 중인 게임이 있습니다!", ephemeral=True)
            return

    double_or_nothing = mode == "Double or Nothing"
    game = BuckshotGame(interaction.user, opponent, double_or_nothing=double_or_nothing)
    current_player = interaction.user if game.current_turn == interaction.user.id else opponent
    game.get_items()  # 초기 아이템 로드
    embed = discord.Embed(
        title=f"벅샷 룰렛 🔫 | 라운드 {game.round}/3 | {current_player.display_name}의 턴 | 모드: {mode}",
        description=f"{interaction.user.mention} vs {opponent.mention}",
        color=discord.Color.red() if game.current_turn == interaction.user.id else discord.Color.blue()
    )
    embed.add_field(
        name=f"{interaction.user.display_name} 체력",
        value=game.get_hp_bar(interaction.user.id, interaction.user.id),
        inline=True
    )
    embed.add_field(
        name=f"{opponent.display_name} 체력",
        value=game.get_hp_bar(opponent.id, interaction.user.id),
        inline=True
    )
    embed.add_field(name="탄환", value=game.get_chamber_info(), inline=True)
    embed.add_field(name=f"{interaction.user.display_name} 아이템", value=", ".join(game.items[interaction.user.id]) or "없음", inline=False)
    embed.add_field(name=f"{opponent.display_name} 아이템", value=", ".join(game.items[opponent.id]) or "없음", inline=False)
    embed.add_field(name="스코어", value=f"{interaction.user.display_name}: {game.scores[interaction.user.id]} | {opponent.display_name}: {game.scores[opponent.id]}", inline=True)

    view = discord.ui.View(timeout=300)

    async def on_timeout():
        game.end_game()
        channel = client.get_channel(interaction.channel_id)
        if game.last_message:
            await game.last_message.edit(content="게임이 타임아웃으로 종료되었습니다.", view=None, embed=None)
        else:
            await channel.send("게임이 타임아웃으로 종료되었습니다.")

    view.on_timeout = on_timeout

    shoot_self = discord.ui.Button(label="자신 쏘기", style=discord.ButtonStyle.red, emoji="🔫")
    async def shoot_self_callback(button_interaction: discord.Interaction):
        if button_interaction.user.id != game.current_turn:
            await button_interaction.response.send_message("당신의 턴이 아닙니다!", ephemeral=True)
            return
        shoot_self.disabled = True
        shoot_opponent.disabled = True
        use_item.disabled = True
        bullet, extra_turn, damage, reload_message, handcuff_used, knife_used, old_hp = game.shoot(button_interaction.user.id, button_interaction.user.id)
        current_player = interaction.user if game.current_turn == interaction.user.id else opponent
        show_chamber = bool(reload_message)
        game.get_items()  # 최신 아이템 로드
        embed = discord.Embed(
            title=f"벅샷 룰렛 🔫 | 라운드 {game.round}/3 | {current_player.display_name}의 턴 | 모드: {mode}",
            description=f"{interaction.user.mention} vs {opponent.mention}",
            color=discord.Color.red() if game.current_turn == interaction.user.id else discord.Color.blue()
        )
        if reload_message:
            embed.add_field(name="장전", value=reload_message, inline=False)
        else:
            result = f"{'🔴 실탄' if bullet == 'live' else '🔵 공포탄'}! "
            if bullet == "live":
                result += f"{button_interaction.user.display_name}이(가) {damage} 피해를 입었습니다! "
                if knife_used:
                    result += "(칼 효과: 대미지 2배) "
                result += f"(체력: {old_hp} → {game.hp[button_interaction.user.id]})"
            else:
                result += f"{button_interaction.user.display_name}에게 피해 없음! (추가 턴)"
            embed.add_field(name="결과", value=result, inline=False)
        embed.add_field(
            name=f"{interaction.user.display_name} 체력",
            value=game.get_hp_bar(interaction.user.id, button_interaction.user.id),
            inline=True
        )
        embed.add_field(
            name=f"{opponent.display_name} 체력",
            value=game.get_hp_bar(opponent.id, button_interaction.user.id),
            inline=True
        )
        if show_chamber:
            embed.add_field(name="탄환", value=game.get_chamber_info(), inline=True)
        embed.add_field(name=f"{interaction.user.display_name} 아이템", value=", ".join(game.items[button_interaction.user.id]) or "없음", inline=False)
        embed.add_field(name=f"{opponent.display_name} 아이템", value=", ".join(game.items[opponent.id]) or "없음", inline=False)
        embed.add_field(name="스코어", value=f"{interaction.user.display_name}: {game.scores[button_interaction.user.id]} | {opponent.display_name}: {game.scores[opponent.id]}", inline=True)

        if game.hp[button_interaction.user.id] <= 0:
            game.scores[opponent.id] += 1
            embed.add_field(name="라운드 종료", value=f"{opponent.display_name}이(가) 라운드 {game.round} 승리!", inline=False)
            game_end = game.check_game_end()
            if game_end:
                embed.add_field(name="게임 종료", value=game_end, inline=False)
                view.clear_items()
                game.end_game()
            else:
                if not game.start_new_round():
                    game_end = game.check_game_end()
                    embed.add_field(name="게임 종료", value=game_end, inline=False)
                    view.clear_items()
                    game.end_game()
                else:
                    current_player = interaction.user if game.current_turn == interaction.user.id else opponent
                    game.get_items()
                    embed = discord.Embed(
                        title=f"벅샷 룰렛 🔫 | 라운드 {game.round}/3 | {current_player.display_name}의 턴 | 모드: {mode}",
                        description=f"{interaction.user.mention} vs {opponent.mention}",
                        color=discord.Color.red() if game.current_turn == interaction.user.id else discord.Color.blue()
                    )
                    embed.add_field(name="새 라운드", value=f"라운드 {game.round} 시작! 체력, 아이템, 탄환이 초기화되었습니다.", inline=False)
                    embed.add_field(
                        name=f"{interaction.user.display_name} 체력",
                        value=game.get_hp_bar(interaction.user.id, button_interaction.user.id),
                        inline=True
                    )
                    embed.add_field(
                        name=f"{opponent.display_name} 체력",
                        value=game.get_hp_bar(opponent.id, button_interaction.user.id),
                        inline=True
                    )
                    embed.add_field(name="탄환", value=game.get_chamber_info(), inline=True)
                    embed.add_field(name=f"{interaction.user.display_name} 아이템", value=", ".join(game.items[button_interaction.user.id]) or "없음", inline=False)
                    embed.add_field(name=f"{opponent.display_name} 아이템", value=", ".join(game.items[opponent.id]) or "없음", inline=False)
                    embed.add_field(name="스코어", value=f"{interaction.user.display_name}: {game.scores[button_interaction.user.id]} | {opponent.display_name}: {game.scores[opponent.id]}", inline=True)
        else:
            if bullet and not extra_turn:
                game.switch_turn()
            current_player = interaction.user if game.current_turn == interaction.user.id else opponent
            embed.title = f"벅샷 룰렛 🔫 | 라운드 {game.round}/3 | {current_player.display_name}의 턴 | 모드: {mode}"
            embed.color = discord.Color.red() if game.current_turn == interaction.user.id else discord.Color.blue()

        shoot_self.disabled = False
        shoot_opponent.disabled = False
        use_item.disabled = False
        if game.last_message:
            await game.last_message.delete()
        game.last_message = await button_interaction.response.send_message(embed=embed, view=view)

    shoot_self.callback = shoot_self_callback
    view.add_item(shoot_self)

    shoot_opponent = discord.ui.Button(label="상대 쏘기", style=discord.ButtonStyle.green, emoji="🎯")
    async def shoot_opponent_callback(button_interaction: discord.Interaction):
        if button_interaction.user.id != game.current_turn:
            await button_interaction.response.send_message("당신의 턴이 아닙니다!", ephemeral=True)
            return
        shoot_self.disabled = True
        shoot_opponent.disabled = True
        use_item.disabled = True
        target_id = opponent.id if button_interaction.user.id == interaction.user.id else interaction.user.id
        bullet, extra_turn, damage, reload_message, handcuff_used, knife_used, old_hp = game.shoot(button_interaction.user.id, target_id)
        current_player = interaction.user if game.current_turn == interaction.user.id else opponent
        show_chamber = bool(reload_message)
        game.get_items()
        embed = discord.Embed(
            title=f"벅샷 룰렛 🔫 | 라운드 {game.round}/3 | {current_player.display_name}의 턴 | 모드: {mode}",
            description=f"{interaction.user.mention} vs {opponent.mention}",
            color=discord.Color.red() if game.current_turn == interaction.user.id else discord.Color.blue()
        )
        if reload_message:
            embed.add_field(name="장전", value=reload_message, inline=False)
        else:
            target_name = opponent.display_name if button_interaction.user.id == interaction.user.id else interaction.user.display_name
            result = f"{'🔴 실탄' if bullet == 'live' else '🔵 공포탄'}! "
            if bullet == "live":
                result += f"{target_name}이(가) {damage} 피해를 입었습니다! "
                if knife_used:
                    result += "(칼 효과: 대미지 2배) "
                result += f"(체력: {old_hp} → {game.hp[target_id]})"
            else:
                result += f"{target_name}에게 피해 없음!"
            embed.add_field(name="결과", value=result, inline=False)
            if handcuff_used:
                embed.add_field(name="수갑 효과", value=f"🔗 {button_interaction.user.display_name}이(가) 수갑으로 턴을 유지했습니다!", inline=False)
        embed.add_field(
            name=f"{interaction.user.display_name} 체력",
            value=game.get_hp_bar(interaction.user.id, button_interaction.user.id),
            inline=True
        )
        embed.add_field(
            name=f"{opponent.display_name} 체력",
            value=game.get_hp_bar(opponent.id, button_interaction.user.id),
            inline=True
        )
        if show_chamber:
            embed.add_field(name="탄환", value=game.get_chamber_info(), inline=True)
        embed.add_field(name=f"{interaction.user.display_name} 아이템", value=", ".join(game.items[button_interaction.user.id]) or "없음", inline=False)
        embed.add_field(name=f"{opponent.display_name} 아이템", value=", ".join(game.items[opponent.id]) or "없음", inline=False)
        embed.add_field(name="스코어", value=f"{interaction.user.display_name}: {game.scores[button_interaction.user.id]} | {opponent.display_name}: {game.scores[opponent.id]}", inline=True)

        if game.hp[target_id] <= 0:
            game.scores[button_interaction.user.id] += 1
            embed.add_field(name="라운드 종료", value=f"{button_interaction.user.display_name}이(가) 라운드 {game.round} 승리!", inline=False)
            game_end = game.check_game_end()
            if game_end:
                embed.add_field(name="게임 종료", value=game_end, inline=False)
                view.clear_items()
                game.end_game()
            else:
                if not game.start_new_round():
                    game_end = game.check_game_end()
                    embed.add_field(name="게임 종료", value=game_end, inline=False)
                    view.clear_items()
                    game.end_game()
                else:
                    current_player = interaction.user if game.current_turn == interaction.user.id else opponent
                    game.get_items()
                    embed = discord.Embed(
                        title=f"벅샷 룰렛 🔫 | 라운드 {game.round}/3 | {current_player.display_name}의 턴 | 모드: {mode}",
                        description=f"{interaction.user.mention} vs {opponent.mention}",
                        color=discord.Color.red() if game.current_turn == interaction.user.id else discord.Color.blue()
                    )
                    embed.add_field(name="새 라운드", value=f"라운드 {game.round} 시작! 체력, 아이템, 탄환이 초기화되었습니다.", inline=False)
                    embed.add_field(
                        name=f"{interaction.user.display_name} 체력",
                        value=game.get_hp_bar(interaction.user.id, button_interaction.user.id),
                        inline=True
                    )
                    embed.add_field(
                        name=f"{opponent.display_name} 체력",
                        value=game.get_hp_bar(opponent.id, button_interaction.user.id),
                        inline=True
                    )
                    embed.add_field(name="탄환", value=game.get_chamber_info(), inline=True)
                    embed.add_field(name=f"{interaction.user.display_name} 아이템", value=", ".join(game.items[button_interaction.user.id]) or "없음", inline=False)
                    embed.add_field(name=f"{opponent.display_name} 아이템", value=", ".join(game.items[opponent.id]) or "없음", inline=False)
                    embed.add_field(name="스코어", value=f"{interaction.user.display_name}: {game.scores[button_interaction.user.id]} | {opponent.display_name}: {game.scores[opponent.id]}", inline=True)
        else:
            if bullet and not handcuff_used:
                game.switch_turn()
            current_player = interaction.user if game.current_turn == interaction.user.id else opponent
            embed.title = f"벅샷 룰렛 🔫 | 라운드 {game.round}/3 | {current_player.display_name}의 턴 | 모드: {mode}"
            embed.color = discord.Color.red() if game.current_turn == interaction.user.id else discord.Color.blue()

        shoot_self.disabled = False
        shoot_opponent.disabled = False
        use_item.disabled = False
        if game.last_message:
            await game.last_message.delete()
        game.last_message = await button_interaction.response.send_message(embed=embed, view=view)

    shoot_opponent.callback = shoot_opponent_callback
    view.add_item(shoot_opponent)

    use_item = discord.ui.Button(label="아이템 사용", style=discord.ButtonStyle.blurple, emoji="🧪")
    async def use_item_callback(button_interaction: discord.Interaction):
        if button_interaction.user.id != game.current_turn:
            await button_interaction.response.send_message("당신의 턴이 아닙니다!", ephemeral=True)
            return
        game.get_items()
        items = game.items[button_interaction.user.id]
        if not items:
            await button_interaction.response.send_message("사용 가능한 아이템이 없습니다!", ephemeral=True)
            return

        item_select = discord.ui.Select(placeholder="아이템을 선택하세요", options=[
            discord.SelectOption(label=item, value=f"{item}_{idx}") for idx, item in enumerate(items)
        ])
        async def item_select_callback(select_interaction: discord.Interaction):
            selected_value = select_interaction.data["values"][0]
            item = selected_value.split("_")[0]
            opponent_id = opponent.id if select_interaction.user.id == interaction.user.id else interaction.user.id
            if item == "주사기" and game.items[opponent_id]:
                opponent_items = game.items[opponent_id]
                steal_select = discord.ui.Select(placeholder="훔칠 아이템을 선택하세요", options=[
                    discord.SelectOption(label=opponent_item, value=f"{opponent_item}_{idx}") for idx, opponent_item in enumerate(opponent_items)
                ])
                async def steal_select_callback(steal_interaction: discord.Interaction):
                    stolen_value = steal_interaction.data["values"][0]
                    stolen_item = stolen_value.split("_")[0]
                    game.get_items()
                    game.items[opponent_id].remove(stolen_item)
                    result = game.use_item(steal_interaction.user.id, stolen_item, opponent_id)
                    game.items[steal_interaction.user.id].remove(item)
                    save_items_to_json(game.game_id, game.player1.id, game.player2.id, game.items)
                    current_player = interaction.user if game.current_turn == interaction.user.id else opponent
                    show_chamber = stolen_item in ["맥주", "인버터"]
                    embed = discord.Embed(
                        title=f"벅샷 룰렛 🔫 | 라운드 {game.round}/3 | {current_player.display_name}의 턴 | 모드: {mode}",
                        description=f"{interaction.user.mention} vs {opponent.mention}",
                        color=discord.Color.red() if game.current_turn == interaction.user.id else discord.Color.blue()
                    )
                    embed.add_field(name="아이템 사용", value=f"주사기: {stolen_item}을(를) 훔쳐 즉시 사용했습니다! {result}", inline=False)
                    embed.add_field(
                        name=f"{interaction.user.display_name} 체력",
                        value=game.get_hp_bar(interaction.user.id, steal_interaction.user.id),
                        inline=True
                    )
                    embed.add_field(
                        name=f"{opponent.display_name} 체력",
                        value=game.get_hp_bar(opponent.id, steal_interaction.user.id),
                        inline=True
                    )
                    if show_chamber:
                        embed.add_field(name="탄환", value=game.get_chamber_info(), inline=True)
                    embed.add_field(name=f"{interaction.user.display_name} 아이템", value=", ".join(game.items[steal_interaction.user.id]) or "없음", inline=False)
                    embed.add_field(name=f"{opponent.display_name} 아이템", value=", ".join(game.items[opponent.id]) or "없음", inline=False)
                    embed.add_field(name="스코어", value=f"{interaction.user.display_name}: {game.scores[steal_interaction.user.id]} | {opponent.display_name}: {game.scores[opponent.id]}", inline=True)

                    shoot_self.disabled = False
                    shoot_opponent.disabled = False
                    use_item.disabled = False
                    if game.last_message:
                        await game.last_message.delete()
                    game.last_message = await steal_interaction.response.send_message(embed=embed, view=view)

                steal_select.callback = steal_select_callback
                steal_view = discord.ui.View()
                steal_view.add_item(steal_select)
                await select_interaction.response.send_message("훔칠 아이템을 선택하세요:", view=steal_view, ephemeral=True)
            else:
                result = game.use_item(select_interaction.user.id, item, opponent_id)
                game.get_items()
                if item in game.items[select_interaction.user.id]:
                    game.items[select_interaction.user.id].remove(item)
                save_items_to_json(game.game_id, game.player1.id, game.player2.id, game.items)
                current_player = interaction.user if game.current_turn == interaction.user.id else opponent
                show_chamber = item in ["맥주", "인버터"]
                embed = discord.Embed(
                    title=f"벅샷 룰렛 🔫 | 라운드 {game.round}/3 | {current_player.display_name}의 턴 | 모드: {mode}",
                    description=f"{interaction.user.mention} vs {opponent.mention}",
                    color=discord.Color.red() if game.current_turn == interaction.user.id else discord.Color.blue()
                )
                if item == "돋보기":
                    await select_interaction.response.send_message(result, ephemeral=True)
                    embed.add_field(name="아이템 사용", value=f"{select_interaction.user.display_name}이(가) 돋보기를 사용했습니다.", inline=False)
                else:
                    embed.add_field(name="아이템 사용", value=result, inline=False)
                embed.add_field(
                    name=f"{interaction.user.display_name} 체력",
                    value=game.get_hp_bar(interaction.user.id, select_interaction.user.id),
                    inline=True
                )
                embed.add_field(
                    name=f"{opponent.display_name} 체력",
                    value=game.get_hp_bar(opponent.id, select_interaction.user.id),
                    inline=True
                )
                if show_chamber:
                    embed.add_field(name="탄환", value=game.get_chamber_info(), inline=True)
                embed.add_field(name=f"{interaction.user.display_name} 아이템", value=", ".join(game.items[select_interaction.user.id]) or "없음", inline=False)
                embed.add_field(name=f"{opponent.display_name} 아이템", value=", ".join(game.items[opponent.id]) or "없음", inline=False)
                embed.add_field(name="스코어", value=f"{interaction.user.display_name}: {game.scores[select_interaction.user.id]} | {opponent.display_name}: {game.scores[opponent.id]}", inline=True)

                shoot_self.disabled = False
                shoot_opponent.disabled = False
                use_item.disabled = False
                if game.last_message:
                    await game.last_message.delete()
                game.last_message = await select_interaction.response.send_message(embed=embed, view=view)

        item_select.callback = item_select_callback
        item_view = discord.ui.View()
        item_view.add_item(item_select)
        await button_interaction.response.send_message("아이템을 선택하세요:", view=item_view, ephemeral=True)

    use_item.callback = use_item_callback
    view.add_item(use_item)

    invite_embed = discord.Embed(title="벅샷 룰렛 초대 🔫", description=f"{opponent.mention}, {interaction.user.mention}이(가) 대결을 요청했습니다! (모드: {mode}) 수락하시겠습니까?")
    invite_view = discord.ui.View()
    accept_button = discord.ui.Button(label="수락", style=discord.ButtonStyle.green, emoji="✅")
    async def accept_callback(button_interaction: discord.Interaction):
        if button_interaction.user.id != opponent.id:
            await button_interaction.response.send_message("당신은 초대를 수락할 수 없습니다!", ephemeral=True)
            return
        game.status = "active" 
        game._save_to_db()
        if game.last_message:
            await game.last_message.delete()
        game.last_message = await button_interaction.response.send_message(embed=embed, view=view)
        invite_view.clear_items()

    accept_button.callback = accept_callback
    invite_view.add_item(accept_button)

    reject_button = discord.ui.Button(label="거절", style=discord.ButtonStyle.red, emoji="❌")
    async def reject_callback(button_interaction: discord.Interaction):
        if button_interaction.user.id != opponent.id:
            await button_interaction.response.send_message("당신은 초대를 거절할 수 없습니다!", ephemeral=True)
            return
        if game.last_message:
            await game.last_message.delete()
        await button_interaction.response.send_message(f"{opponent.display_name}이(가) 초대를 거절했습니다!", ephemeral=False)
        game.end_game()
        invite_view.clear_items()

    reject_button.callback = reject_callback
    invite_view.add_item(reject_button)

    await interaction.response.send_message(embed=invite_embed, view=invite_view)

@tree.command(name="items", description="벅샷 룰렛 게임의 아이템 설명을 확인합니다.")
async def items(interaction: discord.Interaction):
    embed = discord.Embed(
        title="벅샷 룰렛 아이템 설명 🧪",
        description="각 아이템의 효과를 확인하세요!",
        color=discord.Color.purple()
    )
    item_descriptions = {
        "맥주": "샷건에서 현재 탄환을 배출하고 실탄인지 공포탄인지 확인합니다. (-$495)",
        "돋보기": "다음 탄환의 종류(실탄/공포탄)를 확인합니다.",
        "담배": "체력을 1 회복합니다. 최대 체력은 4입니다. 3라운드에서 체력 2 이하 시 사용 불가. (-$220)",
        "칼": "다음 샷의 대미지를 2배로 만듭니다.",
        "수갑": "다음 상대 샷 후에도 턴을 유지합니다.",
        "주사기": "상대의 아이템 하나를 선택해 훔쳐 즉시 사용합니다. (-$3000)",
        "버너폰": "잔탄 3발 이상 시, 마지막 탄환의 종류를 확인합니다. 잔탄 2발 시 '안타깝게... 됐군...' 메시지 출력.",
        "인버터": "다음 탄환의 상태를 반전시킵니다(실탄 ↔ 공포탄).",
        "재머": "상대의 다음 아이템 사용을 무효화합니다.",
        "상한 약": "(Double or Nothing 전용) 50% 확률로 체력 2 회복 또는 체력 1 감소. 최대 체력 초과 불가."
    }
    for item, description in item_descriptions.items():
        embed.add_field(name=item, value=description, inline=False)
    await interaction.response.send_message(embed=embed, ephemeral=True)

@tree.command(name="money", description="현재 보유한 상금을 확인합니다.")
async def money(interaction: discord.Interaction):
    with sqlite3.connect("buckshot.db") as conn:
        c = conn.cursor()
        c.execute("SELECT total_money, item_usage_history FROM player_money WHERE player_id = ?", (interaction.user.id,))
        result = c.fetchone()
    embed = discord.Embed(
        title="상금 정보 💰",
        description=f"{interaction.user.display_name}의 상금 및 아이템 사용 내역",
        color=discord.Color.gold()
    )
    if result:
        total_money, usage_history = result
        usage_history = json.loads(usage_history)
        embed.add_field(name="총 상금", value=f"${total_money:,}", inline=False)
        embed.add_field(name="아이템 사용 내역", value="\n".join([f"{item}: {count}회" for item, count in usage_history.items() if count > 0]) or "없음", inline=False)
    else:
        embed.add_field(name="총 상금", value="$0", inline=False)
        embed.add_field(name="아이템 사용 내역", value="없음", inline=False)
    await interaction.response.send_message(embed=embed, ephemeral=True)

@tree.command(name="init_db", description="벅샷 룰렛 데이터베이스를 초기화합니다. (관리자 전용)")
async def init_db_command(interaction: discord.Interaction):
    if not interaction.user.guild_permissions.administrator:
        await interaction.response.send_message("이 명령어는 관리자만 사용할 수 있습니다!", ephemeral=True)
        return
    success, message = init_db()
    await interaction.response.send_message(message, ephemeral=True)

@client.event
async def on_ready():
    global synced
    print(f'Logged in as {client.user}')
    if not synced:
        try:
            synced_commands = await tree.sync()
            print(f"Global slash commands synced! Synced {len(synced_commands)} commands: {[cmd.name for cmd in synced_commands]}")
            synced = True
        except Exception as e:
            print(f"Failed to sync commands: {e}")
            await asyncio.sleep(5)
            try:
                synced_commands = await tree.sync()
                print(f"Retry successful! Synced {len(synced_commands)} commands: {[cmd.name for cmd in synced_commands]}")
                synced = True
            except Exception as retry_e:
                print(f"Retry failed: {retry_e}")
                
client.run('')
