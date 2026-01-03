package main

import (
	"bytes"
	"database/sql"
	"encoding/json"
	"flag"
	"fmt"
	"io"
	"log"
	"mime/multipart"
	"net/http"
	"os"
	"os/exec"
	"path/filepath"
	"strconv"
	"strings"
	"time"

	"github.com/joho/godotenv"
	_ "github.com/mattn/go-sqlite3"
)

// --- Minimal Telegram client using only stdlib ---
// Types mirror only the fields we need.
type Update struct {
	UpdateID      int            `json:"update_id"`
	Message       *TGMessage     `json:"message,omitempty"`
	CallbackQuery *CallbackQuery `json:"callback_query,omitempty"`
}

type TGMessage struct {
	MessageID int     `json:"message_id"`
	From      *TGUser `json:"from,omitempty"`
	Chat      *TGChat `json:"chat,omitempty"`
	Text      string  `json:"text,omitempty"`
	Date      int64   `json:"date,omitempty"`
}

type TGUser struct {
	ID        int64  `json:"id"`
	FirstName string `json:"first_name"`
	UserName  string `json:"username"`
}

type TGChat struct {
	ID int64 `json:"id"`
}

type CallbackQuery struct {
	ID      string     `json:"id"`
	From    *TGUser    `json:"from"`
	Message *TGMessage `json:"message,omitempty"`
	Data    string     `json:"data,omitempty"`
}

type InlineKeyboardButton struct {
	Text         string `json:"text"`
	CallbackData string `json:"callback_data,omitempty"`
}

type InlineKeyboardMarkup struct {
	InlineKeyboard [][]InlineKeyboardButton `json:"inline_keyboard"`
}

type BotClient struct {
	token      string
	baseURL    string
	httpClient *http.Client
}

func NewBotClient(token string) *BotClient {
	return &BotClient{
		token:      token,
		baseURL:    fmt.Sprintf("https://api.telegram.org/bot%s", token),
		httpClient: &http.Client{Timeout: 60 * time.Second},
	}
}

func (b *BotClient) apiPost(path string, body interface{}, contentType string) ([]byte, error) {
	url := b.baseURL + "/" + path
	var bodyReader io.Reader
	var ct string

	if contentType == "application/json" {
		var buf bytes.Buffer
		if err := json.NewEncoder(&buf).Encode(body); err != nil {
			return nil, err
		}
		bodyReader = &buf
		ct = "application/json"
	} else {
		// body is already an io.Reader for multipart (handled by caller)
		if rdr, ok := body.(io.Reader); ok {
			bodyReader = rdr
			ct = contentType
		} else {
			return nil, fmt.Errorf("unsupported body type for contentType %s", contentType)
		}
	}

	req, err := http.NewRequest("POST", url, bodyReader)
	if err != nil {
		return nil, err
	}
	req.Header.Set("Content-Type", ct)

	resp, err := b.httpClient.Do(req)
	if err != nil {
		return nil, err
	}
	defer resp.Body.Close()
	return io.ReadAll(resp.Body)
}

func (b *BotClient) apiGet(path string, params map[string]string) ([]byte, error) {
	url := b.baseURL + "/" + path
	if params != nil && len(params) > 0 {
		q := "?"
		first := true
		for k, v := range params {
			if !first {
				q += "&"
			}
			q += fmt.Sprintf("%s=%s", k, v)
			first = false
		}
		url += q
	}
	resp, err := b.httpClient.Get(url)
	if err != nil {
		return nil, err
	}
	defer resp.Body.Close()
	return io.ReadAll(resp.Body)
}

func (b *BotClient) GetUpdates(offset int, timeout int) ([]Update, error) {
	params := map[string]string{
		"timeout": strconv.Itoa(timeout),
	}
	if offset > 0 {
		params["offset"] = strconv.Itoa(offset)
	}
	data, err := b.apiGet("getUpdates", params)
	if err != nil {
		return nil, err
	}
	var result struct {
		OK     bool     `json:"ok"`
		Result []Update `json:"result"`
	}
	if err := json.Unmarshal(data, &result); err != nil {
		return nil, err
	}
	return result.Result, nil
}

func (b *BotClient) SendMessage(chatID int64, text string, replyMarkup interface{}) (*TGMessage, error) {
	payload := map[string]interface{}{
		"chat_id": chatID,
		"text":    text,
	}
	if replyMarkup != nil {
		payload["reply_markup"] = replyMarkup
	}
	data, err := b.apiPost("sendMessage", payload, "application/json")
	if err != nil {
		return nil, err
	}
	var result struct {
		OK     bool       `json:"ok"`
		Result *TGMessage `json:"result"`
	}
	if err := json.Unmarshal(data, &result); err != nil {
		return nil, err
	}
	return result.Result, nil
}

func (b *BotClient) EditMessageText(chatID int64, messageID int, text string, replyMarkup interface{}) (*TGMessage, error) {
	payload := map[string]interface{}{
		"chat_id":    chatID,
		"message_id": messageID,
		"text":       text,
	}
	if replyMarkup != nil {
		payload["reply_markup"] = replyMarkup
	}
	data, err := b.apiPost("editMessageText", payload, "application/json")
	if err != nil {
		return nil, err
	}
	var result struct {
		OK     bool       `json:"ok"`
		Result *TGMessage `json:"result"`
	}
	if err := json.Unmarshal(data, &result); err != nil {
		return nil, err
	}
	return result.Result, nil
}

func (b *BotClient) AnswerCallbackQuery(callbackID string, text string) error {
	payload := map[string]interface{}{
		"callback_query_id": callbackID,
		"text":              text,
	}
	_, err := b.apiPost("answerCallbackQuery", payload, "application/json")
	return err
}

// SendPhoto uploads a local file (photoPath) and sends it to chatID with optional caption
func (b *BotClient) SendPhoto(chatID int64, photoPath string, caption string) (*TGMessage, error) {
	url := b.baseURL + "/sendPhoto"
	var buf bytes.Buffer
	w := multipart.NewWriter(&buf)

	_ = w.WriteField("chat_id", strconv.FormatInt(chatID, 10))
	if caption != "" {
		_ = w.WriteField("caption", caption)
	}

	fw, err := w.CreateFormFile("photo", filepath.Base(photoPath))
	if err != nil {
		return nil, err
	}
	file, err := os.Open(photoPath)
	if err != nil {
		return nil, err
	}
	defer file.Close()
	if _, err := io.Copy(fw, file); err != nil {
		return nil, err
	}
	w.Close()

	returned, err := b.apiPost(url[len(b.baseURL)+1:], &buf, w.FormDataContentType())
	if err != nil {
		return nil, err
	}
	var result struct {
		OK     bool       `json:"ok"`
		Result *TGMessage `json:"result"`
	}
	if err := json.Unmarshal(returned, &result); err != nil {
		return nil, err
	}
	return result.Result, nil
}

// --- End minimal telegram client ---

var (
	API_TOKEN       string
	ALLOWED_USER_ID int64
	DB_PATH         string
	categories      []string
	botClient       *BotClient
	db              *sql.DB
)

type TransactionState struct {
	UserID          int64
	Step            string // Tracks current state step
	TransactionType string // "income" or "expense"
	Category        string
	Amount          float64
	Description     string
	EditID          int64 // ID of transaction being edited/deleted
	PromptMessageID int   // message id that was edited to prompt user (used to remove keyboard / show confirmation)
}

var userStates = make(map[int64]*TransactionState)

func main() {
	var err error

	// Load env vars (optional)
	if err = godotenv.Load(); err != nil {
		log.Println("No .env file found, continuing")
	}

	// Flags
	dataPath := flag.String("data", "", "Path to database file")
	flag.Parse()

	API_TOKEN = os.Getenv("API_TOKEN")
	ALLOWED_USER_ID, _ = strconv.ParseInt(os.Getenv("ALLOWED_USER_ID"), 10, 64)

	if *dataPath != "" {
		DB_PATH = *dataPath
	} else {
		DB_PATH = os.Getenv("DB_PATH")
	}

	if DB_PATH == "" {
		log.Fatal("DB path must be provided via --data or DB_PATH env var")
	}

	// Init bot client (stdlib)
	botClient = NewBotClient(API_TOKEN)
	// Try to get bot info (optional)
	if info, err := botClient.apiGet("getMe", nil); err == nil {
		var me struct {
			OK     bool            `json:"ok"`
			Result json.RawMessage `json:"result"`
		}
		_ = json.Unmarshal(info, &me)
		// We don't strictly need it; just log success
		log.Println("Telegram client initialized (getMe ok)")
	} else {
		log.Printf("Failed to call getMe: %v", err)
	}

	// Init DB
	db, err = sql.Open("sqlite3", DB_PATH)
	if err != nil {
		log.Panic(err)
	}
	defer db.Close()

	if err := initDB(db); err != nil {
		log.Panic(err)
	}

	if err := seedCategories(db); err != nil {
		log.Panic(err)
	}

	categories, err = loadCategories(db)
	if err != nil {
		log.Panic(err)
	}

	log.Printf("Loaded categories: %s", strings.Join(categories, ", "))

	// Long-polling loop
	offset := 0
	for {
		updates, err := botClient.GetUpdates(offset, 60)
		if err != nil {
			log.Printf("GetUpdates error: %v", err)
			time.Sleep(2 * time.Second)
			continue
		}
		for _, update := range updates {
			if update.Message != nil {
				handleMessage(update.Message)
			} else if update.CallbackQuery != nil {
				handleCallbackQuery(update.CallbackQuery)
			}
			offset = update.UpdateID + 1
		}
	}
}

// Helper to build keyboard in our InlineKeyboardMarkup shape
func buildKeyboard(rows [][]InlineKeyboardButton) InlineKeyboardMarkup {
	return InlineKeyboardMarkup{InlineKeyboard: rows}
}

func getCategories() ([]string, error) {
	return loadCategories(db)
}

func initDB(db *sql.DB) error {
	queries := []string{
		`CREATE TABLE IF NOT EXISTS categories (
			id INTEGER PRIMARY KEY AUTOINCREMENT,
			name TEXT NOT NULL UNIQUE,
			created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
		)`,
		`CREATE TABLE IF NOT EXISTS transactions (
			id INTEGER PRIMARY KEY AUTOINCREMENT,
			type TEXT NOT NULL,
			category TEXT NOT NULL,
			amount REAL NOT NULL,
			description TEXT,
			created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
		)`,
	}

	for _, q := range queries {
		if _, err := db.Exec(q); err != nil {
			return err
		}
	}
	return nil
}

func seedCategories(db *sql.DB) error {
	defaultCategories := []string{
		"Food",
		"Salary",
		"Needs",
		"Water",
		"Laundry",
		"Transportation",
		"Utilities",
		"Rent",
		"Bills",
	}

	tx, err := db.Begin()
	if err != nil {
		return err
	}
	defer tx.Rollback()

	stmt, err := tx.Prepare(`INSERT OR IGNORE INTO categories (name) VALUES (?)`)
	if err != nil {
		return err
	}
	defer stmt.Close()

	for _, cat := range defaultCategories {
		if _, err := stmt.Exec(cat); err != nil {
			return err
		}
	}

	return tx.Commit()
}

func loadCategories(db *sql.DB) ([]string, error) {
	rows, err := db.Query(`SELECT name FROM categories ORDER BY name`)
	if err != nil {
		return nil, err
	}
	defer rows.Close()

	var result []string
	for rows.Next() {
		var name string
		if err := rows.Scan(&name); err != nil {
			return nil, err
		}
		result = append(result, name)
	}
	return result, nil
}

// Message handlers adapted to stdlib types
func handleMessage(message *TGMessage) {
	if message.From == nil {
		return
	}
	userID := message.From.ID
	if userID != ALLOWED_USER_ID {
		sendMessage(message.Chat.ID, "You are not authorized to use this bot.")
		return
	}

	// Detect commands: Telegram sends text like "/add" in message.Text
	text := strings.TrimSpace(message.Text)
	command := ""
	args := ""
	if text != "" && strings.HasPrefix(text, "/") {
		parts := strings.SplitN(text, " ", 2)
		command = strings.TrimPrefix(parts[0], "/")
		if len(parts) > 1 {
			args = parts[1]
		}
	}

	switch command {
	case "add":
		startTransaction(message.Chat.ID, userID)
	case "summary":
		showSummary(message.Chat.ID)
	case "get_latest_report":
		get_latest_report(message.Chat.ID)
	case "get_weekly_expense":
		get_weekly_expense_report(message.Chat.ID)
	case "get_weekly_expense_piechart":
		get_weekly_expense_piechart(message.Chat.ID)
	case "edit":
		args = strings.TrimSpace(args)
		if args != "" {
			id, err := strconv.ParseInt(args, 10, 64)
			if err != nil {
				sendMessage(message.Chat.ID, "Invalid ID provided. Usage: /edit <id>")
				return
			}
			startEditWithID(message.Chat.ID, userID, id)
		} else {
			startEdit(message.Chat.ID, userID)
		}
	case "delete":
		args = strings.TrimSpace(args)
		if args != "" {
			id, err := strconv.ParseInt(args, 10, 64)
			if err != nil {
				sendMessage(message.Chat.ID, "Invalid ID provided. Usage: /delete <id>")
				return
			}
			startDeleteWithID(message.Chat.ID, userID, id)
		} else {
			startDelete(message.Chat.ID, userID)
		}
	default:
		if state, exists := userStates[userID]; exists {
			switch state.Step {
			case "ENTER_AMOUNT":
				processAmount(message, state)
			case "ENTER_DESCRIPTION":
				processDescription(message, state)
			case "ENTER_EDIT_ID":
				processEditId(message, state)
			case "ENTER_EDIT_AMOUNT":
				processEditAmountEdit(message, state)
			case "ENTER_EDIT_DESCRIPTION":
				processEditDescriptionEdit(message, state)
			case "ENTER_DELETE_ID":
				processDeleteId(message, state)
			default:
				sendMessage(message.Chat.ID, "I don't understand that command.")
			}
		} else {
			sendMessage(message.Chat.ID, "I don't understand that command.")
		}
	}
}

func handleCallbackQuery(callback *CallbackQuery) {
	userID := callback.From.ID
	if userID != ALLOWED_USER_ID {
		sendMessage(callback.Message.Chat.ID, "You are not authorized to use this bot.")
		return
	}

	state, exists := userStates[userID]
	if !exists {
		// If there's no state but callback comes from edit/delete menu, ignore
		_ = botClient.AnswerCallbackQuery(callback.ID, "")
		return
	}

	// Remove "loading" state in client
	_ = botClient.AnswerCallbackQuery(callback.ID, "")

	switch state.Step {
	case "SELECT_TYPE":
		processTransactionType(callback, state)
	case "SELECT_CATEGORY":
		processCategory(callback, state)
	case "SELECT_EDIT_FIELD":
		processEditField(callback, state)
	case "SELECT_EDIT_TYPE":
		processEditTransactionType(callback, state)
	case "SELECT_EDIT_CATEGORY":
		processEditCategory(callback, state)
	case "CONFIRM_DELETE":
		processDeleteConfirmation(callback, state)
	default:
		// no-op
	}
}

func startTransaction(chatID int64, userID int64) {
	state := &TransactionState{
		UserID: userID,
		Step:   "SELECT_TYPE",
	}
	userStates[userID] = state

	buttons := [][]InlineKeyboardButton{
		{
			InlineKeyboardButton{Text: "Income", CallbackData: "income"},
			InlineKeyboardButton{Text: "Expense", CallbackData: "expense"},
		},
	}
	keyboard := buildKeyboard(buttons)
	sendMessageWithKeyboard(chatID, "Please choose the type of transaction:", keyboard)
}

func processTransactionType(callback *CallbackQuery, state *TransactionState) {
	state.TransactionType = callback.Data
	state.Step = "SELECT_CATEGORY"

	buttons := make([][]InlineKeyboardButton, 0)
	for _, category := range categories {
		buttons = append(buttons, []InlineKeyboardButton{
			{Text: category, CallbackData: category},
		})
	}
	keyboard := buildKeyboard(buttons)
	editMessageWithKeyboard(callback.Message.Chat.ID, callback.Message.MessageID, fmt.Sprintf("You selected %s. Choose a category:", state.TransactionType), keyboard)
}

func processCategory(callback *CallbackQuery, state *TransactionState) {
	state.Category = callback.Data
	state.Step = "ENTER_AMOUNT"

	editMessage(callback.Message.Chat.ID, callback.Message.MessageID, fmt.Sprintf("Selected category: %s. Enter the transaction amount.", state.Category))
}

func processAmount(message *TGMessage, state *TransactionState) {
	amount, err := strconv.ParseFloat(message.Text, 64)
	if err != nil || amount <= 0 {
		sendMessage(message.Chat.ID, "Invalid amount. Please enter a positive number.")
		return
	}

	state.Amount = amount
	state.Step = "ENTER_DESCRIPTION"
	sendMessage(message.Chat.ID, "Enter a description for the transaction (max 100 characters).")
}

func processDescription(message *TGMessage, state *TransactionState) {
	if len(message.Text) > 100 {
		sendMessage(message.Chat.ID, "Description too long. Please keep it under 100 characters.")
		return
	}

	state.Description = message.Text

	// Get current time in GMT+7
	currentTime := time.Now().In(time.FixedZone("GMT+7", 7*60*60))

	stmt, err := db.Prepare("INSERT INTO transactions (type, category, amount, description, created_at) VALUES (?, ?, ?, ?, ?)")
	if err != nil {
		sendMessage(message.Chat.ID, "Failed to prepare transaction.")
		log.Printf("Database prepare error: %v", err)
		return
	}
	defer stmt.Close()

	_, err = stmt.Exec(state.TransactionType, state.Category, state.Amount, state.Description, currentTime.Format("2006-01-02 15:04:05"))
	if err != nil {
		sendMessage(message.Chat.ID, "Failed to save transaction.")
		log.Printf("Database exec error: %v", err)
		return
	}

	delete(userStates, state.UserID)
	sendMessage(message.Chat.ID, "Transaction added successfully!")
}

func showSummary(chatID int64) {
	currentMonth := time.Now().UTC().Format("01")
	rows, err := db.Query("SELECT type, SUM(amount) as total FROM transactions WHERE strftime('%m', created_at) = ? GROUP BY type", currentMonth)
	if err != nil {
		sendMessage(chatID, "Error retrieving transactions.")
		log.Printf("Database query error: %v", err)
		return
	}
	defer rows.Close()

	incomeTotal := 0.0
	expenseTotal := 0.0
	for rows.Next() {
		var transactionType string
		var total float64
		err := rows.Scan(&transactionType, &total)
		if err != nil {
			log.Printf("Row scan error: %v", err)
			continue
		}
		if transactionType == "income" {
			incomeTotal = total
		} else if transactionType == "expense" {
			expenseTotal = total
		}
	}

	if err = rows.Err(); err != nil {
		log.Printf("Rows error: %v", err)
	}

	balance := incomeTotal - expenseTotal
	summaryMessage := fmt.Sprintf("Monthly Summary Report for %s:\n\n", time.Now().Format("January 2006"))
	summaryMessage += fmt.Sprintf("Total Income: %.2f\nTotal Expense: %.2f\n\nBalance: %.2f",
		incomeTotal, expenseTotal, balance)
	sendMessage(chatID, summaryMessage)
}

// sendMessage wrapper to use botClient
func sendMessage(chatID int64, text string) {
	_, err := botClient.SendMessage(chatID, text, nil)
	if err != nil {
		log.Printf("Error sending message: %v", err)
	}
}

func sendMessageWithKeyboard(chatID int64, text string, keyboard InlineKeyboardMarkup) {
	_, err := botClient.SendMessage(chatID, text, keyboard)
	if err != nil {
		log.Printf("Error sending message with keyboard: %v", err)
	}
}

func editMessage(chatID int64, messageID int, text string) {
	_, err := botClient.EditMessageText(chatID, messageID, text, nil)
	if err != nil {
		log.Printf("Error editing message: %v", err)
	}
}

func editMessageWithKeyboard(chatID int64, messageID int, text string, keyboard InlineKeyboardMarkup) {
	_, err := botClient.EditMessageText(chatID, messageID, text, keyboard)
	if err != nil {
		log.Printf("Error editing message with keyboard: %v", err)
	}
}

func get_latest_report(chatID int64) {
	cmd := exec.Command("python3", "src/g_latest_r.py") // Path to your Python script
	output, err := cmd.CombinedOutput()
	if err != nil {
		log.Printf("Error executing Python script: %s", err)
		sendMessage(chatID, "Failed to execute the report.")
		return
	}

	sendMessage(chatID, string(output))
}

func get_weekly_expense_report(chatID int64) {
	cmd := exec.Command("python3", "src/g_weekly_e_r.py")
	output, err := cmd.CombinedOutput()
	if err != nil {
		log.Printf("Error executing Python script: %s", err)
		sendMessage(chatID, "Failed to execute the report.")
		return
	}

	sendMessage(chatID, string(output))
}

func get_weekly_expense_piechart(chatID int64) {
	// Keep same behavior as before: run external python script with API_TOKEN env.
	// The Python may send image using API_TOKEN, or print path/output; we relay output.
	cmd := exec.Command("python3", "src/g_w_e_piechart.py", fmt.Sprintf("%d", chatID))
	cmd.Env = append(os.Environ(), fmt.Sprintf("API_TOKEN=%s", API_TOKEN))
	output, err := cmd.CombinedOutput()
	if err != nil {
		log.Printf("Error executing piechart script: %v, output: %s", err, string(output))
		sendMessage(chatID, "Failed to run piechart script. Check logs.")
		return
	}
	// If script prints something useful, send it
	if len(output) > 0 {
		sendMessage(chatID, string(output))
	}
}

/*
	EDIT / UPDATE feature
*/

// startEdit initiates the interactive edit flow asking for ID
func startEdit(chatID int64, userID int64) {
	state := &TransactionState{
		UserID: userID,
		Step:   "ENTER_EDIT_ID",
	}
	userStates[userID] = state
	sendMessage(chatID, "Please enter the transaction ID you want to edit.")
}

// startEditWithID begins edit flow immediately when ID is already provided
func startEditWithID(chatID int64, userID int64, id int64) {
	row := db.QueryRow("SELECT id, type, category, amount, description, created_at FROM transactions WHERE id = ?", id)
	var (
		rid         int64
		typ         string
		category    string
		amount      float64
		description sql.NullString
		createdAt   string
	)
	err := row.Scan(&rid, &typ, &category, &amount, &description, &createdAt)
	if err != nil {
		if err == sql.ErrNoRows {
			sendMessage(chatID, fmt.Sprintf("Transaction with ID %d not found.", id))
			return
		}
		sendMessage(chatID, "Failed to retrieve transaction.")
		log.Printf("DB scan error: %v", err)
		return
	}

	state := &TransactionState{
		UserID:          userID,
		Step:            "SELECT_EDIT_FIELD",
		EditID:          id,
		TransactionType: typ,
		Category:         category,
		Amount:           amount,
	}
	if description.Valid {
		state.Description = description.String
	}
	userStates[userID] = state

	details := fmt.Sprintf("Transaction ID: %d\nType: %s\nCategory: %s\nAmount: %.2f\nDescription: %s\n\nChoose field to edit:",
		id, typ, category, amount, state.Description)
	buttons := [][]InlineKeyboardButton{
		{
			{Text: "Edit Type", CallbackData: "edit_field:type"},
			{Text: "Edit Category", CallbackData: "edit_field:category"},
		},
		{
			{Text: "Edit Amount", CallbackData: "edit_field:amount"},
			{Text: "Edit Description", CallbackData: "edit_field:description"},
		},
	}
	keyboard := buildKeyboard(buttons)
	sendMessageWithKeyboard(chatID, details, keyboard)
}

// processEditId handles user input for the ID to edit
func processEditId(message *TGMessage, state *TransactionState) {
	id, err := strconv.ParseInt(strings.TrimSpace(message.Text), 10, 64)
	if err != nil || id <= 0 {
		sendMessage(message.Chat.ID, "Invalid ID. Please enter a valid transaction ID number.")
		return
	}

	row := db.QueryRow("SELECT id, type, category, amount, description, created_at FROM transactions WHERE id = ?", id)
	var (
		rid         int64
		typ         string
		category    string
		amount      float64
		description sql.NullString
		createdAt   string
	)
	err = row.Scan(&rid, &typ, &category, &amount, &description, &createdAt)
	if err != nil {
		if err == sql.ErrNoRows {
			sendMessage(message.Chat.ID, fmt.Sprintf("Transaction with ID %d not found.", id))
			return
		}
		sendMessage(message.Chat.ID, "Failed to retrieve transaction.")
		log.Printf("DB scan error: %v", err)
		return
	}

	state.EditID = id
	state.TransactionType = typ
	state.Category = category
	state.Amount = amount
	if description.Valid {
		state.Description = description.String
	}
	state.Step = "SELECT_EDIT_FIELD"

	details := fmt.Sprintf("Transaction ID: %d\nType: %s\nCategory: %s\nAmount: %.2f\nDescription: %s\n\nChoose field to edit:",
		id, typ, category, amount, state.Description)
	buttons := [][]InlineKeyboardButton{
		{
			{Text: "Edit Type", CallbackData: "edit_field:type"},
			{Text: "Edit Category", CallbackData: "edit_field:category"},
		},
		{
			{Text: "Edit Amount", CallbackData: "edit_field:amount"},
			{Text: "Edit Description", CallbackData: "edit_field:description"},
		},
	}
	keyboard := buildKeyboard(buttons)
	sendMessageWithKeyboard(message.Chat.ID, details, keyboard)
}

// processEditField handles the callback when user selects which field to edit
func processEditField(callback *CallbackQuery, state *TransactionState) {
	parts := strings.SplitN(callback.Data, ":", 2)
	if len(parts) != 2 {
		sendMessage(callback.Message.Chat.ID, "Invalid selection.")
		return
	}
	field := parts[1]

	switch field {
	case "type":
		state.Step = "SELECT_EDIT_TYPE"
		state.PromptMessageID = callback.Message.MessageID
		buttons := [][]InlineKeyboardButton{
			{
				{Text: "Income", CallbackData: "income"},
				{Text: "Expense", CallbackData: "expense"},
			},
			{
				{Text: "Cancel", CallbackData: "edit_cancel"},
			},
		}
		keyboard := buildKeyboard(buttons)
		editMessageWithKeyboard(callback.Message.Chat.ID, callback.Message.MessageID, "Select new type:", keyboard)
	case "category":
		state.Step = "SELECT_EDIT_CATEGORY"
		state.PromptMessageID = callback.Message.MessageID
		buttons := make([][]InlineKeyboardButton, 0)
		for _, category := range categories {
			buttons = append(buttons, []InlineKeyboardButton{
				{Text: category, CallbackData: category},
			})
		}
		buttons = append(buttons, []InlineKeyboardButton{
			{Text: "Cancel", CallbackData: "edit_cancel"},
		})
		keyboard := buildKeyboard(buttons)
		editMessageWithKeyboard(callback.Message.Chat.ID, callback.Message.MessageID, "Select new category:", keyboard)
	case "amount":
		state.Step = "ENTER_EDIT_AMOUNT"
		state.PromptMessageID = callback.Message.MessageID
		editMessage(callback.Message.Chat.ID, callback.Message.MessageID, "Enter new amount (positive number):")
	case "description":
		state.Step = "ENTER_EDIT_DESCRIPTION"
		state.PromptMessageID = callback.Message.MessageID
		editMessage(callback.Message.Chat.ID, callback.Message.MessageID, "Enter new description (max 100 characters):")
	default:
		sendMessage(callback.Message.Chat.ID, "Unknown field selected.")
	}
}

// processEditTransactionType handles callback when user selects new type for edit
func processEditTransactionType(callback *CallbackQuery, state *TransactionState) {
	newType := callback.Data
	chatID := callback.Message.Chat.ID
	msgID := callback.Message.MessageID

	if newType == "edit_cancel" {
		editMessage(chatID, msgID, "Edit canceled.")
		delete(userStates, state.UserID)
		return
	}

	_, err := db.Exec("UPDATE transactions SET type = ? WHERE id = ?", newType, state.EditID)
	if err != nil {
		log.Printf("Failed to update type: %v", err)
		editMessage(chatID, msgID, "Failed to update transaction type.")
		delete(userStates, state.UserID)
		return
	}
	editMessage(chatID, msgID, fmt.Sprintf("Transaction %d updated: type set to %s", state.EditID, newType))
	delete(userStates, state.UserID)
}

// processEditCategory handles callback when user selects new category for edit
func processEditCategory(callback *CallbackQuery, state *TransactionState) {
	newCategory := callback.Data
	chatID := callback.Message.Chat.ID
	msgID := callback.Message.MessageID

	if newCategory == "edit_cancel" {
		editMessage(chatID, msgID, "Edit canceled.")
		delete(userStates, state.UserID)
		return
	}

	_, err := db.Exec("UPDATE transactions SET category = ? WHERE id = ?", newCategory, state.EditID)
	if err != nil {
		log.Printf("Failed to update category: %v", err)
		editMessage(chatID, msgID, "Failed to update transaction category.")
		delete(userStates, state.UserID)
		return
	}
	editMessage(chatID, msgID, fmt.Sprintf("Transaction %d updated: category set to %s", state.EditID, newCategory))
	delete(userStates, state.UserID)
}

// processEditAmountEdit handles updating amount after user inputs it
func processEditAmountEdit(message *TGMessage, state *TransactionState) {
	amount, err := strconv.ParseFloat(message.Text, 64)
	if err != nil || amount <= 0 {
		sendMessage(message.Chat.ID, "Invalid amount. Please enter a positive number.")
		return
	}
	_, err = db.Exec("UPDATE transactions SET amount = ? WHERE id = ?", amount, state.EditID)
	if err != nil {
		log.Printf("Failed to update amount: %v", err)
		if state.PromptMessageID != 0 {
			editMessage(message.Chat.ID, state.PromptMessageID, "Failed to update transaction amount.")
		} else {
			sendMessage(message.Chat.ID, "Failed to update transaction amount.")
		}
		delete(userStates, state.UserID)
		return
	}

	if state.PromptMessageID != 0 {
		editMessage(message.Chat.ID, state.PromptMessageID, fmt.Sprintf("Transaction %d updated: amount set to %.2f", state.EditID, amount))
	} else {
		sendMessage(message.Chat.ID, fmt.Sprintf("Transaction %d updated: amount set to %.2f", state.EditID, amount))
	}

	delete(userStates, state.UserID)
}

// processEditDescriptionEdit handles updating description after user inputs it
func processEditDescriptionEdit(message *TGMessage, state *TransactionState) {
	if len(message.Text) > 100 {
		sendMessage(message.Chat.ID, "Description too long. Please keep it under 100 characters.")
		return
	}
	_, err := db.Exec("UPDATE transactions SET description = ? WHERE id = ?", message.Text, state.EditID)
	if err != nil {
		log.Printf("Failed to update description: %v", err)
		if state.PromptMessageID != 0 {
			editMessage(message.Chat.ID, state.PromptMessageID, "Failed to update transaction description.")
		} else {
			sendMessage(message.Chat.ID, "Failed to update transaction description.")
		}
		delete(userStates, state.UserID)
		return
	}

	if state.PromptMessageID != 0 {
		editMessage(message.Chat.ID, state.PromptMessageID, fmt.Sprintf("Transaction %d updated: description set.", state.EditID))
	} else {
		sendMessage(message.Chat.ID, fmt.Sprintf("Transaction %d updated: description set.", state.EditID))
	}

	delete(userStates, state.UserID)
}

/*
	DELETE feature with confirmation
*/

// startDelete asks for an ID to delete
func startDelete(chatID int64, userID int64) {
	state := &TransactionState{
		UserID: userID,
		Step:   "ENTER_DELETE_ID",
	}
	userStates[userID] = state
	sendMessage(chatID, "Please enter the transaction ID you want to delete.")
}

// startDeleteWithID begins delete flow immediately when ID is already provided
func startDeleteWithID(chatID int64, userID int64, id int64) {
	row := db.QueryRow("SELECT id, type, category, amount, description, created_at FROM transactions WHERE id = ?", id)
	var (
		rid         int64
		typ         string
		category    string
		amount      float64
		description sql.NullString
		createdAt   string
	)
	err := row.Scan(&rid, &typ, &category, &amount, &description, &createdAt)
	if err != nil {
		if err == sql.ErrNoRows {
			sendMessage(chatID, fmt.Sprintf("Transaction with ID %d not found.", id))
			return
		}
		sendMessage(chatID, "Failed to retrieve transaction.")
		log.Printf("DB scan error: %v", err)
		return
	}

	state := &TransactionState{
		UserID:          userID,
		Step:            "CONFIRM_DELETE",
		EditID:          id,
		TransactionType: typ,
		Category:        category,
		Amount:          amount,
	}
	if description.Valid {
		state.Description = description.String
	}
	userStates[userID] = state

	details := fmt.Sprintf("Transaction ID: %d\nType: %s\nCategory: %s\nAmount: %.2f\nDescription: %s\n\nAre you sure you want to DELETE this transaction?",
		id, typ, category, amount, state.Description)
	buttons := [][]InlineKeyboardButton{
		{
			{Text: "Confirm Delete", CallbackData: "delete_confirm"},
			{Text: "Cancel", CallbackData: "delete_cancel"},
		},
	}
	keyboard := buildKeyboard(buttons)
	sendMessageWithKeyboard(chatID, details, keyboard)
}

// processDeleteId handles user input for the ID to delete
func processDeleteId(message *TGMessage, state *TransactionState) {
	id, err := strconv.ParseInt(strings.TrimSpace(message.Text), 10, 64)
	if err != nil || id <= 0 {
		sendMessage(message.Chat.ID, "Invalid ID. Please enter a valid transaction ID number.")
		return
	}

	row := db.QueryRow("SELECT id, type, category, amount, description, created_at FROM transactions WHERE id = ?", id)
	var (
		rid         int64
		typ         string
		category    string
		amount      float64
		description sql.NullString
		createdAt   string
	)
	err = row.Scan(&rid, &typ, &category, &amount, &description, &createdAt)
	if err != nil {
		if err == sql.ErrNoRows {
			sendMessage(message.Chat.ID, fmt.Sprintf("Transaction with ID %d not found.", id))
			return
		}
		sendMessage(message.Chat.ID, "Failed to retrieve transaction.")
		log.Printf("DB scan error: %v", err)
		return
	}

	state.EditID = id
	state.TransactionType = typ
	state.Category = category
	state.Amount = amount
	if description.Valid {
		state.Description = description.String
	}
	state.Step = "CONFIRM_DELETE"

	details := fmt.Sprintf("Transaction ID: %d\nType: %s\nCategory: %s\nAmount: %.2f\nDescription: %s\n\nAre you sure you want to DELETE this transaction?",
		id, typ, category, amount, state.Description)
	buttons := [][]InlineKeyboardButton{
		{
			{Text: "Confirm Delete", CallbackData: "delete_confirm"},
			{Text: "Cancel", CallbackData: "delete_cancel"},
		},
	}
	keyboard := buildKeyboard(buttons)
	sendMessageWithKeyboard(message.Chat.ID, details, keyboard)
}

// processDeleteConfirmation handles callback when user confirms or cancels deletion
func processDeleteConfirmation(callback *CallbackQuery, state *TransactionState) {
	chatID := callback.Message.Chat.ID
	msgID := callback.Message.MessageID

	switch callback.Data {
	case "delete_confirm":
		res, err := db.Exec("DELETE FROM transactions WHERE id = ?", state.EditID)
		if err != nil {
			log.Printf("Failed to delete transaction %d: %v", state.EditID, err)
			editMessage(chatID, msgID, fmt.Sprintf("Failed to delete transaction %d.", state.EditID))
			delete(userStates, state.UserID)
			return
		}
		rowsAffected, _ := res.RowsAffected()
		if rowsAffected == 0 {
			editMessage(chatID, msgID, fmt.Sprintf("No transaction deleted. ID %d may not exist.", state.EditID))
		} else {
			editMessage(chatID, msgID, fmt.Sprintf("Transaction %d has been deleted.", state.EditID))
		}
		delete(userStates, state.UserID)
	case "delete_cancel":
		editMessage(chatID, msgID, "Deletion canceled.")
		delete(userStates, state.UserID)
	default:
		editMessage(chatID, msgID, "Unknown selection. No action taken.")
	}
}
